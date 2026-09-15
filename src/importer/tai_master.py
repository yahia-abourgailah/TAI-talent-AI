"""The TAI_Master workbook, imported: workbook -> object storage and raw.capture -> core (B2).

    python -m importer.tai_master --by "<your name>" [--master PATH] [--sheet NAME]

Runs as a job, so every run is recorded in audit.job_run with its counts, its skips and what it
left unresolved (look it up with python -m jobs show JOB_ID). The whole import is one transaction.

Importing the same workbook again writes nothing: captures, candidates, fields and evaluations
that exist are left as they are. Nothing is ever overwritten. If a row imported from an earlier
workbook has changed, its candidate is left untouched and the row is listed as unresolved.
"""

import argparse
import hashlib
import os
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Connection

from importer.blobs import BlobStore, s3_store
from importer.rows import (
    CRITERIA_VERSION,
    FIELD_SOURCE,
    RAW_ONLY_COLUMNS,
    SOURCE,
    PreparedRow,
    prepare_row,
)
from jobs.queue import RunLog, claim, engine_from_environment, enqueue, find_runs, run_job
from replay.baseline import inside_git_repository
from replay.workbook import MasterSheet, read_master

JOB_KIND = "tai_master_import"
WORKBOOK_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
ROW_MEDIA_TYPE = "application/json"

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
_FIND_CANDIDATE = text("SELECT id, capture_id FROM core.candidate WHERE source_key = :key")
_INSERT_CANDIDATE = text(
    """
    INSERT INTO core.candidate (capture_id, source_key, created_by, pipeline_state)
    VALUES (:capture_id, :key, :created_by, :pipeline_state)
    RETURNING id
    """
)
_INSERT_FIELD = text(
    """
    INSERT INTO core.candidate_field
      (candidate_id, field, value, source, source_ref, verification_status, inference)
    VALUES (:candidate_id, :field, :value, :source, :source_ref, :status, :inference)
    ON CONFLICT (candidate_id, field) DO NOTHING
    RETURNING id
    """
)
_INSERT_EVALUATION = text(
    """
    INSERT INTO core.evaluation
      (candidate_id, criteria_version_id, origin, score, tier, recommendation, signals, flags,
       call_priority, recorded_by)
    VALUES
      (:candidate_id, :version, 'stored', :score, :tier, :recommendation, :signals, :flags,
       :call_priority, :recorded_by)
    ON CONFLICT (candidate_id, criteria_version_id, origin) DO NOTHING
    RETURNING id
    """
)


def import_workbook(
    conn: Connection,
    sheet: MasterSheet,
    workbook: bytes,
    blobs: BlobStore,
    actor: str,
    log: RunLog,
    source: str = SOURCE,
) -> None:
    if hashlib.sha256(workbook).hexdigest() != sheet.sha256:
        raise ValueError("The workbook changed while it was being read. Run the import again.")
    log.input_sha256 = sheet.sha256
    log.count("rows_read", len(sheet.rows))
    log.count("blank_rows_ignored", sheet.blank_rows_skipped)
    blobs.put_if_absent(f"{source}/workbooks/{sheet.sha256}.xlsx", workbook, WORKBOOK_MEDIA_TYPE)
    for row in sheet.rows:
        _import_row(conn, prepare_row(sheet, row, source), blobs, actor, log, source)


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

    blob_key = f"{source}/rows/{row.payload_sha256.hex()}.json"
    blobs.put_if_absent(blob_key, row.payload, ROW_MEDIA_TYPE)
    capture = {"source": source, "external_id": row.external_id, "sha": row.payload_sha256}
    capture_id = conn.execute(
        _INSERT_CAPTURE,
        {
            **capture,
            "blob_key": blob_key,
            "media_type": ROW_MEDIA_TYPE,
            "byte_size": len(row.payload),
            "received_by": actor,
        },
    ).scalar_one_or_none()
    if capture_id is None:
        capture_id = conn.execute(_FIND_CAPTURE, capture).scalar_one()
        log.count("raw_captures_existing")
    else:
        log.count("raw_captures_new")

    found = conn.execute(_FIND_CANDIDATE, {"key": row.source_key}).one_or_none()
    if found is None:
        candidate_id = conn.execute(
            _INSERT_CANDIDATE,
            {
                "capture_id": capture_id,
                "key": row.source_key,
                "created_by": actor,
                "pipeline_state": "recorded_in_raw" if row.raw_only_filled else "not_recorded",
            },
        ).scalar_one()
        log.count("candidates_new")
    elif found.capture_id != capture_id:
        log.count("candidates_left_unchanged")
        log.unresolve("row_differs_from_earlier_import", sheet_row=row.sheet_row)
        return
    else:
        candidate_id = found.id
        log.count("candidates_existing")

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
            "flags": None if stored.flags is None else list(stored.flags),
            "call_priority": stored.call_priority,
            "recorded_by": actor,
        },
    ).scalar_one_or_none()
    log.count("evaluations_new" if written is not None else "evaluations_existing")


def master_path(value: Any) -> Path:
    raw = str(value or os.environ.get("TALENT_MASTER_PATH") or "")
    if not raw:
        raise ValueError("Set TALENT_MASTER_PATH or pass --master.")
    master = Path(raw).expanduser()
    if inside_git_repository(master):
        raise ValueError(
            f"{master.name} is inside a git repository. Candidate data must live outside it "
            "(docs/DATA_HANDLING.md)."
        )
    return master


def handle(conn: Connection, params: Mapping[str, Any], actor: str, log: RunLog) -> None:
    """The job handler for tai_master_import."""
    master = master_path(params.get("master"))
    sheet = read_master(master, sheet=params.get("sheet"))
    import_workbook(conn, sheet, master.read_bytes(), s3_store(), actor, log)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m importer.tai_master")
    parser.add_argument("--master", help="the workbook (default: $TALENT_MASTER_PATH)")
    parser.add_argument("--sheet", help="sheet name (default: the first sheet)")
    parser.add_argument("--by", required=True, help="the person running the import")
    args = parser.parse_args(argv)

    engine = engine_from_environment()
    with engine.begin() as conn:
        job_id = enqueue(conn, JOB_KIND, {"master": args.master, "sheet": args.sheet}, args.by)
    with engine.begin() as conn:
        job = claim(conn, job_id)
        if job is None:
            print(f"error: job {job_id} was claimed by another worker.", file=sys.stderr)
            return 1
        run_job(conn, job, {JOB_KIND: handle})
        (run,) = find_runs(conn, job_id=job_id, limit=1)

    print(f"Job {job_id}: {run['outcome']}.")
    for name, value in sorted(run["counts"].items()):
        print(f"  {name:<28} {value:>7,}")
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
