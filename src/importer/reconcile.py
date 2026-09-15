"""The import, checked against the workbook (B5). The report holds counts only.

    python -m importer.reconcile --out DIR [--master PATH] [--sample 200] [--seed 20260914]
        [--report-copy docs/migration/RECONCILIATION_REPORT.md]

Every check compares the database, and the raw captures read back from object storage, with the
workbook's cells. It does not reuse the importer's row preparation, so a mistake in the import
cannot confirm itself.

  rows         non-blank sheet rows = candidates imported from them
  workbook     the workbook file is kept as a raw capture under its SHA-256
  raw          each candidate's raw capture, read back, holds exactly the row's cells today
  fields       per column and row: the current stored value equals the cell, and a blank or a
               "no value" marker is stored as not recorded
  evaluations  per scored row: score, tier, recommendation, call priority and the original Signals
               and Flags text equal the sheet, under 2026-08-04
  replay       the replay of 2026-08-04 reaches parity and reads the scores the database holds
  sample       random rows, sheet beside database, for a person to check by hand. The report never
               says the sample was checked; the person who checks it records that.

The sample holds candidate data, so it goes outside the repository and is written owner-only.
"""

import argparse
import csv
import hashlib
import io
import json
import os
import random
import sys
from datetime import date, datetime, time
from decimal import Decimal
from pathlib import Path
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Connection

from importer.blobs import BlobStore, s3_store
from importer.paths import check_outside_repository, master_path
from importer.rows import (
    CALL_PRIORITY,
    CRITERIA_VERSION,
    FIELD_COLUMNS,
    FLAGS,
    NO_VALUE_MARKERS,
    RAW_ONLY_COLUMNS,
    RECOMMENDATION,
    SCORE,
    SIGNALS,
    SOURCE,
    TIER,
    missing_columns,
)
from jobs.queue import ReportableError, engine_from_environment
from replay.baseline import run_baseline
from replay.results import apply_rulings
from replay.rulings import RULINGS
from replay.workbook import MasterRow, MasterSheet, WorkbookError, read_master

# The date CI's parity job pins, so both read the same replay.
REPLAY_RUN_DATE = date(2026, 9, 14)
SAMPLE_COLUMNS = (
    "sheet_row",
    "column",
    "sheet_value",
    "expected_value",
    "expected_status",
    "stored_value",
    "stored_status",
    "inference",
    "tool_agrees",
    "checked_by",
    "note",
)
_OWN_ROWS = "left(c.source_key, length(:prefix)) = :prefix"


def _prefix(source: str) -> dict[str, str]:
    return {"prefix": f"{source}:row:"}


def _blank(value: Any) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


def _shown(value: Any) -> str:
    """How a cell reads as text, written here independently of the importer."""
    if isinstance(value, datetime | date | time):
        return value.isoformat()
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def expected_field(column: str, value: Any) -> tuple[str | None, str]:
    """What the database should hold for this cell: (value, verification status)."""
    if _blank(value):
        return None, "not_recorded"
    shown = _shown(value)
    if shown.strip().lower() in NO_VALUE_MARKERS.get(column, frozenset()):
        return None, "not_recorded"
    return shown, "unverified"


def _expected_text(value: Any) -> str | None:
    return None if _blank(value) else _shown(value)


def _whole_score(value: Any) -> int | None:
    """A stored score the import should have kept: a whole number from 0 to 100."""
    if _blank(value) or isinstance(value, bool):
        return None
    try:
        parsed = Decimal(str(value).strip())
    except ArithmeticError:
        return None
    if not parsed.is_finite() or parsed != parsed.to_integral_value() or not 0 <= parsed <= 100:
        return None
    return int(parsed)


def _decode(typed: dict[str, Any] | None) -> Any:
    if typed is None:
        return None
    kind, value = typed["type"], typed["value"]
    if kind == "float":
        return float(value)
    if kind == "datetime":
        return datetime.fromisoformat(value)
    if kind == "date":
        return date.fromisoformat(value)
    if kind == "time":
        return time.fromisoformat(value)
    return value  # bool, int and str keep their JSON types


def _cells_match(row: MasterRow, payload: bytes) -> bool:
    try:
        body = json.loads(payload)
        cells = [(column, _decode(typed)) for column, typed in body["cells"]]
    except (ValueError, KeyError, TypeError):
        return False
    return bool(body.get("sheet_row") == row.sheet_row and cells == list(row.values.items()))


def reconcile(
    conn: Connection, sheet: MasterSheet, blobs: BlobStore, source: str = SOURCE
) -> dict[str, Any]:
    total = len(sheet.rows)
    rows_by_key = {f"{source}:row:{row.sheet_row}": row for row in sheet.rows}

    captured = {
        found.source_key: found
        for found in conn.execute(
            text(
                f"""
                SELECT c.source_key, r.content_sha256, r.blob_key
                FROM core.candidate c JOIN raw.capture r ON r.id = c.capture_id
                WHERE {_OWN_ROWS}
                """
            ),
            _prefix(source),
        )
    }
    raw_equal = 0
    for key, row in rows_by_key.items():
        found = captured.get(key)
        if found is None:
            continue
        payload = blobs.get(found.blob_key)
        if (
            payload is not None
            and hashlib.sha256(payload).digest() == bytes(found.content_sha256)
            and _cells_match(row, payload)
        ):
            raw_equal += 1
    workbook_captured = (
        conn.execute(
            text(
                "SELECT count(*) FROM raw.capture "
                "WHERE source = :source AND external_id = :external_id AND content_sha256 = :sha"
            ),
            {
                "source": source,
                "external_id": f"workbook:{sheet.sha256}",
                "sha": bytes.fromhex(sheet.sha256),
            },
        ).scalar_one()
        == 1
    )

    stored_fields = {
        (found.source_key, found.field): (found.value, found.verification_status)
        for found in conn.execute(
            text(
                f"""
                SELECT c.source_key, f.field, f.value, f.verification_status
                FROM core.candidate_field_current f JOIN core.candidate c ON c.id = f.candidate_id
                WHERE {_OWN_ROWS}
                """
            ),
            _prefix(source),
        )
    }
    fields = []
    for column, field, _inference in FIELD_COLUMNS:
        filled = markers = stored_values = not_recorded = equal = 0
        for key, row in rows_by_key.items():
            cell = row.values.get(column)
            expected = expected_field(column, cell)
            if not _blank(cell):
                filled += 1
                markers += expected[1] == "not_recorded"
            got = stored_fields.get((key, field))
            if got is not None:
                stored_values += got[0] is not None
                not_recorded += got[1] == "not_recorded"
            equal += got == expected
        fields.append(
            {
                "column": column,
                "field": field,
                "sheet_filled": filled,
                "sheet_no_value_markers": markers,
                "stored_values": stored_values,
                "stored_not_recorded": not_recorded,
                "rows_equal_to_sheet": equal,
                "match": equal == total,
            }
        )
    raw_only = [
        {
            "column": column,
            "sheet_filled": sum(not _blank(row.values.get(column)) for row in sheet.rows),
            "waiting_on": waiting_on,
        }
        for column, waiting_on in RAW_ONLY_COLUMNS.items()
    ]

    scored = {
        key: score
        for key, row in rows_by_key.items()
        if (score := _whole_score(row.values.get(SCORE))) is not None
    }
    evaluations = {
        found.source_key: found
        for found in conn.execute(
            text(
                f"""
                SELECT c.source_key, e.score, e.tier, e.recommendation, e.call_priority,
                       e.signals_text, e.flags_text
                FROM core.evaluation e JOIN core.candidate c ON c.id = e.candidate_id
                WHERE e.criteria_version_id = :version AND e.origin = 'stored' AND {_OWN_ROWS}
                """
            ),
            {"version": CRITERIA_VERSION, **_prefix(source)},
        )
    }
    equal_evaluations = 0
    for key, score in scored.items():
        found = evaluations.get(key)
        values = rows_by_key[key].values
        if (
            found is not None
            and found.score is not None
            and Decimal(found.score) == score
            and (
                found.tier,
                found.recommendation,
                found.call_priority,
                found.signals_text,
                found.flags_text,
            )
            == tuple(
                _expected_text(values.get(column))
                for column in (TIER, RECOMMENDATION, CALL_PRIORITY, SIGNALS, FLAGS)
            )
        ):
            equal_evaluations += 1
    not_in_sheet = len(set(evaluations) - set(scored))

    results, _stale = apply_rulings(run_baseline(sheet, REPLAY_RUN_DATE), sheet.sha256, RULINGS)
    replay_scores = {
        f"{source}:row:{r.sheet_row}": r.stored.score for r in results if r.has_stored_score
    }
    database_scores = {
        key: int(found.score) for key, found in evaluations.items() if found.score is not None
    }
    unexplained = sum(r.unexplained_difference for r in results)

    summary: dict[str, Any] = {
        "workbook_sha256": sheet.sha256,
        "missing_columns": missing_columns(sheet),
        "rows": {
            "sheet": total,
            "candidates": len(captured),
            "match": set(captured) == set(rows_by_key),
        },
        "workbook_capture": {"match": workbook_captured},
        "raw_captures": {"rows": total, "equal_to_sheet": raw_equal, "match": raw_equal == total},
        "fields": fields,
        "raw_only_columns": raw_only,
        "evaluations": {
            "sheet_scores": sum(not _blank(row.values.get(SCORE)) for row in sheet.rows),
            "whole_number_scores": len(scored),
            "stored": len(evaluations),
            "equal_to_sheet": equal_evaluations,
            "not_in_sheet": not_in_sheet,
            "match": len(evaluations) == len(scored) == equal_evaluations and not not_in_sheet,
        },
        "replay": {
            "criteria_version": CRITERIA_VERSION,
            "run_date": REPLAY_RUN_DATE.isoformat(),
            "stored_scores": sum(r.has_stored_score for r in results),
            "score_matches": sum(r.score_match for r in results),
            "ruled_differences": sum(r.ruling is not None for r in results),
            "unexplained_differences": unexplained,
            "reads_the_same_scores_as_the_database": replay_scores == database_scores,
            "parity": unexplained == 0,
        },
    }
    summary["counts_match"] = bool(
        not summary["missing_columns"]
        and summary["rows"]["match"]
        and workbook_captured
        and summary["raw_captures"]["match"]
        and all(f["match"] for f in fields)
        and summary["evaluations"]["match"]
        and summary["replay"]["reads_the_same_scores_as_the_database"]
    )
    return summary


def _line(
    row: MasterRow,
    column: str,
    cell: Any,
    expected: tuple[str | None, str],
    stored: tuple[Any, str, str | None],
    agrees: bool,
) -> dict[str, Any]:
    stored_value, stored_status, inference = stored
    return {
        "sheet_row": row.sheet_row,
        "column": column,
        "sheet_value": "" if _blank(cell) else _shown(cell),
        "expected_value": expected[0] or "",
        "expected_status": expected[1],
        "stored_value": "" if stored_value is None else str(stored_value),
        "stored_status": stored_status,
        "inference": inference or "",
        "tool_agrees": "yes" if agrees else "no",
        "checked_by": "",
        "note": "",
    }


def sample_rows(
    conn: Connection, sheet: MasterSheet, size: int, seed: int, source: str = SOURCE
) -> list[dict[str, Any]]:
    """Sheet beside database for random rows, for a person to check by hand."""
    chosen = sorted(
        random.Random(seed).sample(list(sheet.rows), min(size, len(sheet.rows))),
        key=lambda row: row.sheet_row,
    )
    keys = [f"{source}:row:{row.sheet_row}" for row in chosen]
    fields = {
        (found.source_key, found.field): found
        for found in conn.execute(
            text(
                "SELECT c.source_key, f.field, f.value, f.verification_status, f.inference "
                "FROM core.candidate_field_current f "
                "JOIN core.candidate c ON c.id = f.candidate_id WHERE c.source_key = ANY(:keys)"
            ),
            {"keys": keys},
        )
    }
    evaluations = {
        found.source_key: found
        for found in conn.execute(
            text(
                "SELECT c.source_key, e.score, e.tier, e.recommendation "
                "FROM core.evaluation e JOIN core.candidate c ON c.id = e.candidate_id "
                "WHERE e.criteria_version_id = :version AND e.origin = 'stored' "
                "AND c.source_key = ANY(:keys)"
            ),
            {"version": CRITERIA_VERSION, "keys": keys},
        )
    }

    lines: list[dict[str, Any]] = []
    for row, key in zip(chosen, keys, strict=True):
        for column, field, _inference in FIELD_COLUMNS:
            cell = row.values.get(column)
            expected = expected_field(column, cell)
            got = fields.get((key, field))
            stored = (
                (None, "missing", None)
                if got is None
                else (got.value, got.verification_status, got.inference)
            )
            agrees = got is not None and (got.value, got.verification_status) == expected
            lines.append(_line(row, column, cell, expected, stored, agrees))

        whole = _whole_score(row.values.get(SCORE))
        found = evaluations.get(key)
        for column, attribute in (
            (SCORE, "score"),
            (TIER, "tier"),
            (RECOMMENDATION, "recommendation"),
        ):
            cell = row.values.get(column)
            if whole is None:
                expected = (None, "no evaluation")
            else:
                wanted = str(whole) if column == SCORE else _expected_text(cell)
                expected = (wanted, "stored evaluation")
            value = None if found is None else getattr(found, attribute)
            if isinstance(value, Decimal):
                value = str(int(value)) if value == value.to_integral_value() else str(value)
            stored = (value, "no evaluation" if found is None else "stored evaluation", None)
            agrees = (
                (found is None) if whole is None else (found is not None and value == expected[0])
            )
            lines.append(_line(row, column, cell, expected, stored, agrees))
    return lines


def render_report(summary: dict[str, Any], sample_size: int, seed: int) -> str:
    def mark(ok: bool) -> str:
        return "yes" if ok else "**NO**"

    rows, raw, evaluations, replay = (
        summary["rows"],
        summary["raw_captures"],
        summary["evaluations"],
        summary["replay"],
    )
    workbook_ok = summary["workbook_capture"]["match"]
    lines = [
        "# TAI_Master import reconciliation",
        "",
        f"Workbook SHA-256 `{summary['workbook_sha256']}`. Counts only; no candidate values.",
        "",
        f"**Counts match: {mark(summary['counts_match'])}.** Every check below compares the "
        "database, and the raw captures read back from storage, with the workbook's cells.",
        "",
        f"The {sample_size}-row sample (seed {seed}) is written outside the repository for a "
        "person to check by hand. This report does not record that check; whoever does it "
        "records it in docs/migration/WEEK2_DECISIONS.md.",
        "",
    ]
    if summary["missing_columns"]:
        lines += [f"**Missing columns:** {', '.join(summary['missing_columns'])}.", ""]
    lines += [
        "## Rows, raw captures and evaluations",
        "",
        "| Check | Sheet | Database | Match |",
        "|---|---:|---:|---|",
        f"| Non-blank rows vs candidates | {rows['sheet']:,} | {rows['candidates']:,} "
        f"| {mark(rows['match'])} |",
        f"| Workbook file kept as a raw capture | 1 | {1 if workbook_ok else 0} "
        f"| {mark(workbook_ok)} |",
        f"| Rows whose raw capture, read back, holds the row's cells | {raw['rows']:,} "
        f"| {raw['equal_to_sheet']:,} | {mark(raw['match'])} |",
        f"| Whole-number scores from 0 to 100 vs evaluations under {replay['criteria_version']} "
        f"| {evaluations['whole_number_scores']:,} | {evaluations['stored']:,} "
        f"| {mark(evaluations['match'])} |",
        "| Evaluations equal to the sheet (score, tier, recommendation, call priority, "
        f"Signals and Flags text) | {evaluations['whole_number_scores']:,} "
        f"| {evaluations['equal_to_sheet']:,} | {mark(evaluations['match'])} |",
        "",
        f"Score cells in the sheet: {evaluations['sheet_scores']:,}. A score that is not a whole "
        "number from 0 to 100 is listed as unresolved by the import, not imported.",
        "",
        "## Replay",
        "",
        f"Criteria {replay['criteria_version']} replayed as of {replay['run_date']}: "
        f"{replay['score_matches']:,} of {replay['stored_scores']:,} stored scores match exactly, "
        f"{replay['ruled_differences']:,} differences are ruled, "
        f"{replay['unexplained_differences']:,} unexplained. "
        f"Parity: {mark(replay['parity'])}. "
        "The replay reads the same stored scores the database holds: "
        f"{mark(replay['reads_the_same_scores_as_the_database'])}.",
        "",
        "## Fields",
        "",
        '| Column | Field | Filled in sheet | "No value" markers | Stored values | Not recorded '
        "| Rows equal to sheet | Match |",
        "|---|---|---:|---:|---:|---:|---:|---|",
    ]
    for f in summary["fields"]:
        lines.append(
            f"| {f['column']} | `{f['field']}` | {f['sheet_filled']:,} "
            f"| {f['sheet_no_value_markers']:,} | {f['stored_values']:,} "
            f"| {f['stored_not_recorded']:,} | {f['rows_equal_to_sheet']:,} | {mark(f['match'])} |"
        )
    lines += [
        "",
        "## Kept in the raw capture only",
        "",
        "These columns have no structured home yet. Every cell of them is in each row's raw "
        "capture, which is checked above.",
        "",
        "| Column | Filled in sheet | Waiting on |",
        "|---|---:|---|",
    ]
    lines += [
        f"| {c['column']} | {c['sheet_filled']:,} | {c['waiting_on']} |"
        for c in summary["raw_only_columns"]
    ]
    return "\n".join(lines) + "\n"


def _write_private(path: Path, content: str) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as handle:
        handle.write(content)
    os.chmod(path, 0o600)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m importer.reconcile")
    parser.add_argument("--master", help="the workbook (default: $TALENT_MASTER_PATH)")
    parser.add_argument("--sheet", help="sheet name (default: the first sheet)")
    parser.add_argument("--out", type=Path, required=True, help="outside any git repository")
    parser.add_argument("--sample", type=int, default=200)
    parser.add_argument("--seed", type=int, default=20260914)
    parser.add_argument("--report-copy", type=Path, help="counts only, so it may be committed")
    args = parser.parse_args(argv)

    out_dir = args.out.expanduser()
    try:
        check_outside_repository(out_dir, os.environ.get("TALENT_HOST_RECONCILE_DIR"), "--out")
        master = master_path(args.master)
        sheet = read_master(master, sheet=args.sheet)
    except (ReportableError, WorkbookError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    missing = missing_columns(sheet)
    if missing:
        print(f"error: the sheet is missing columns: {', '.join(missing)}.", file=sys.stderr)
        return 2

    with engine_from_environment().connect() as conn:
        summary = reconcile(conn, sheet, s3_store())
        lines = sample_rows(conn, sheet, args.sample, args.seed)

    out_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    stem = f"reconcile-{sheet.sha256[:12]}"
    report = render_report(summary, args.sample, args.seed)
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=SAMPLE_COLUMNS)
    writer.writeheader()
    writer.writerows(lines)
    _write_private(out_dir / f"{stem}-sample.csv", buffer.getvalue())
    _write_private(out_dir / f"{stem}.json", json.dumps(summary, indent=2) + "\n")
    _write_private(out_dir / f"{stem}.md", report)
    if args.report_copy is not None:
        args.report_copy.parent.mkdir(parents=True, exist_ok=True)
        args.report_copy.write_text(report, encoding="utf-8")

    disagreements = sum(line["tool_agrees"] == "no" for line in lines)
    print(f"Counts match: {'yes' if summary['counts_match'] else 'NO'}.")
    print(f"Replay parity: {'yes' if summary['replay']['parity'] else 'no'}.")
    print(
        f"Sample: {len({line['sheet_row'] for line in lines})} rows, "
        f"{disagreements} disagreements found by the tool. Still to check by hand."
    )
    print(f"  report  {out_dir / (stem + '.md')}")
    print(f"  sample  {out_dir / (stem + '-sample.csv')}  (candidate data, never commit)")
    return 0 if summary["counts_match"] and disagreements == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
