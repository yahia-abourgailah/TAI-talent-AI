"""The TAI_Master workbook, imported: workbook -> object storage and raw.capture -> core (B2).

    python -m importer.tai_master --by "<your name>" [--master PATH] [--sheet NAME]

Runs as a job, so every run is recorded in audit.job_run with its counts, its skips and what it
left unresolved (look it up with python -m jobs show JOB_ID). The whole import is one transaction:
if it stops part-way, nothing from that attempt is kept, and the next run records the stopped
attempt before starting again.

The workbook file is kept as its own raw capture. Each row is kept as a capture keyed by its sheet
row and a hash of its cells, so importing the same cells again writes nothing, even from a re-saved
or re-exported file. Nothing is ever overwritten: if a row's cells differ from the row imported
earlier under the same sheet row, nothing is written for that row and it is listed as unresolved.
"""

import argparse
import hashlib
import sys
from collections.abc import Mapping
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Connection

from importer.blobs import BlobStore, s3_store
from importer.paths import master_path
from importer.rows import (
    CRITERIA_VERSION,
    FIELD_SOURCE,
    RAW_ONLY_COLUMNS,
    SOURCE,
    PreparedRow,
    missing_columns,
    prepare_row,
)
from jobs.queue import (
    ReportableError,
    RunLog,
    engine_from_environment,
    enqueue,
    find_queued,
    recover_stopped,
    work_one,
)
from replay.workbook import MasterSheet, WorkbookError, read_master

JOB_KIND = "tai_master_import"
WORKBOOK_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
ROW_MEDIA_TYPE = "application/json"


class ImportRefused(ReportableError):
    """The import cannot run as asked. The message names files and columns, never people."""


_INSERT_CAPTURE = text(
    """
    INSERT INTO raw.capture
      (source, external_id, content_sha256, blob_key, media_type, byte_size, received_by)
    VALUES (:source, :external_id, :sha, :blob_key, :media_type, :byte_size, :received_by)
    ON CONFLICT DO NOTHING
    RETURNING id
    """
)
_FIND_CAPTURE = text(
    """
    SELECT id FROM raw.capture
    WHERE source = :source AND coalesce(external_id, '') = :external_id AND content_sha256 = :sha
    """
)
_FIND_CANDIDATE = text(
    """
    SELECT c.id, c.capture_id, r.content_sha256
    FROM core.candidate c JOIN raw.capture r ON r.id = c.capture_id
    WHERE c.source_key = :key
    """
)
_INSERT_CANDIDATE = text(
    """
    INSERT INTO core.candidate (capture_id, source_key, created_by, pipeline_state)
    VALUES (:capture_id, :key, :created_by, :pipeline_state)
    RETURNING id
    """
)
# The arbiter names the partial index candidate_field_imported_once, so its predicate is literal.
_INSERT_FIELD = text(
    f"""
    INSERT INTO core.candidate_field
      (candidate_id, field, value, source, source_ref, verification_status, inference, recorded_by)
    VALUES
      (:candidate_id, :field, :value, :source, :source_ref, :status, :inference, :recorded_by)
    -- The index this matches covers unchecked import rows only, so that a person can check one
    -- without it looking like a second import (migration 0017).
    ON CONFLICT (candidate_id, field)
      WHERE source = '{FIELD_SOURCE}' AND verification_status IS DISTINCT FROM 'verified'
      DO NOTHING
    RETURNING id
    """
)
_INSERT_EVALUATION = text(
    """
    INSERT INTO core.evaluation
      (candidate_id, criteria_version_id, origin, score, tier, recommendation, signals,
       signals_text, flags, flags_text, call_priority, recorded_by)
    VALUES
      (:candidate_id, :version, 'stored', :score, :tier, :recommendation, :signals,
       :signals_text, :flags, :flags_text, :call_priority, :recorded_by)
    ON CONFLICT (candidate_id, criteria_version_id) WHERE origin = 'stored' DO NOTHING
    RETURNING id
    """
)


def _capture(
    conn: Connection,
    *,
    source: str,
    external_id: str,
    sha: bytes,
    blob_key: str,
    media_type: str,
    byte_size: int,
    actor: str,
) -> tuple[int, bool]:
    """Inserts a raw capture unless it exists. Returns its id and whether it was new."""
    key = {"source": source, "external_id": external_id, "sha": sha}
    new_id = conn.execute(
        _INSERT_CAPTURE,
        {
            **key,
            "blob_key": blob_key,
            "media_type": media_type,
            "byte_size": byte_size,
            "received_by": actor,
        },
    ).scalar_one_or_none()
    if new_id is not None:
        return int(new_id), True
    return int(conn.execute(_FIND_CAPTURE, key).scalar_one()), False


def import_workbook(
    conn: Connection,
    sheet: MasterSheet,
    workbook: bytes,
    blobs: BlobStore,
    actor: str,
    log: RunLog,
    source: str = SOURCE,
) -> None:
    missing = missing_columns(sheet)
    if missing:
        raise ImportRefused(f"The sheet is missing columns the import reads: {', '.join(missing)}.")
    if hashlib.sha256(workbook).hexdigest() != sheet.sha256:
        raise ImportRefused("The workbook changed while it was being read. Run the import again.")
    log.input_sha256 = sheet.sha256
    log.count("rows_read", len(sheet.rows))
    log.count("blank_rows_ignored", sheet.blank_rows_skipped)

    workbook_key = f"{source}/workbooks/{sheet.sha256}.xlsx"
    blobs.put_if_absent(workbook_key, workbook, WORKBOOK_MEDIA_TYPE)
    _, new = _capture(
        conn,
        source=source,
        external_id=f"workbook:{sheet.sha256}",
        sha=bytes.fromhex(sheet.sha256),
        blob_key=workbook_key,
        media_type=WORKBOOK_MEDIA_TYPE,
        byte_size=len(workbook),
        actor=actor,
    )
    log.count("workbook_captures_new" if new else "workbook_captures_existing")
    for row in sheet.rows:
        _import_row(conn, prepare_row(row, source), blobs, actor, log, source)


def _import_row(
    conn: Connection,
    row: PreparedRow,
    blobs: BlobStore,
    actor: str,
    log: RunLog,
    source: str,
) -> None:
    for code in row.unresolved:
        log.unresolve(code, sheet_row=row.sheet_row)
    for column in row.raw_only_filled:
        log.skip(f"{column}: kept in the raw capture only, waiting on {RAW_ONLY_COLUMNS[column]}")
    for column in row.unnamed_filled:
        log.skip(f"{column}: a cell outside the named columns, kept in the raw capture only")

    found = conn.execute(_FIND_CANDIDATE, {"key": row.source_key}).one_or_none()
    if found is not None and bytes(found.content_sha256) != row.payload_sha256:
        # Never overwrite: a changed row waits for a person, and nothing is written for it.
        log.count("rows_differing_from_earlier_import")
        log.unresolve("row_differs_from_earlier_import", sheet_row=row.sheet_row)
        return

    if found is not None:
        capture_id, candidate_id = int(found.capture_id), int(found.id)
        log.count("raw_captures_existing")
        log.count("candidates_existing")
    else:
        blob_key = f"{source}/rows/{row.payload_sha256.hex()}.json"
        blobs.put_if_absent(blob_key, row.payload, ROW_MEDIA_TYPE)
        capture_id, new = _capture(
            conn,
            source=source,
            external_id=row.external_id,
            sha=row.payload_sha256,
            blob_key=blob_key,
            media_type=ROW_MEDIA_TYPE,
            byte_size=len(row.payload),
            actor=actor,
        )
        log.count("raw_captures_new" if new else "raw_captures_existing")
        candidate_id = int(
            conn.execute(
                _INSERT_CANDIDATE,
                {
                    "capture_id": capture_id,
                    "key": row.source_key,
                    "created_by": actor,
                    "pipeline_state": "recorded_in_raw" if row.raw_only_filled else "not_recorded",
                },
            ).scalar_one()
        )
        log.count("candidates_new")

    for field in row.fields:
        written = conn.execute(
            _INSERT_FIELD,
            {
                "candidate_id": candidate_id,
                "field": field.field,
                "value": field.value,
                "source": FIELD_SOURCE,
                "source_ref": f"raw.capture:{capture_id}#{field.column}",
                "status": field.status,
                "inference": field.inference,
                "recorded_by": actor,
            },
        ).scalar_one_or_none()
        log.count("fields_new" if written is not None else "fields_existing")

    stored = row.evaluation
    if stored is None:
        log.count("rows_without_stored_score")
        return
    written = conn.execute(
        _INSERT_EVALUATION,
        {
            "candidate_id": candidate_id,
            "version": CRITERIA_VERSION,
            "score": stored.score,
            "tier": stored.tier,
            "recommendation": stored.recommendation,
            "signals": None if stored.signals is None else list(stored.signals),
            "signals_text": stored.signals_text,
            "flags": None if stored.flags is None else list(stored.flags),
            "flags_text": stored.flags_text,
            "call_priority": stored.call_priority,
            "recorded_by": actor,
        },
    ).scalar_one_or_none()
    log.count("evaluations_new" if written is not None else "evaluations_existing")


def handle(conn: Connection, params: Mapping[str, Any], actor: str, log: RunLog) -> None:
    """The job handler for tai_master_import."""
    master = master_path(params.get("master"))
    try:
        sheet = read_master(master, sheet=params.get("sheet"))
    except WorkbookError as exc:
        raise ImportRefused(str(exc)) from exc
    import_workbook(conn, sheet, master.read_bytes(), s3_store(), actor, log)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m importer.tai_master")
    parser.add_argument("--master", help="the workbook (default: $TALENT_MASTER_PATH)")
    parser.add_argument("--sheet", help="sheet name (default: the first sheet)")
    parser.add_argument("--by", required=True, help="the person running the import")
    args = parser.parse_args(argv)

    engine = engine_from_environment()
    for stopped, status in recover_stopped(engine, JOB_KIND):
        print(
            f"Job {stopped}: its worker had stopped. Recorded as a failed run; now {status}.",
            file=sys.stderr,
        )
    params = {"master": args.master, "sheet": args.sheet}
    with engine.begin() as conn:
        job_id = find_queued(conn, JOB_KIND, params) or enqueue(conn, JOB_KIND, params, args.by)
    result = work_one(engine, {JOB_KIND: handle}, job_id)
    if result is None:
        print(f"error: job {job_id} is already being run by another worker.", file=sys.stderr)
        return 1
    _, run = result

    print(f"Job {job_id}: {run['outcome']}.")
    for name, value in sorted(run["counts"].items()):
        print(f"  {name:<34} {value:>7,}")
    if run["skipped"]:
        print("Skipped:")
        for reason, value in sorted(run["skipped"].items()):
            print(f"  {value:>7,}  {reason}")
    print(f"Unresolved: {len(run['unresolved'])}. Full record: python -m jobs show {job_id}")
    if run["error"]:
        print(f"error: {run['error']}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
