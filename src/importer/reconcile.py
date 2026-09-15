"""The import, checked against the workbook (B5). The report holds counts only.

    python -m importer.reconcile --out DIR [--master PATH] [--sample 200] [--seed 20260914]
        [--report-copy docs/migration/RECONCILIATION_REPORT.md]

Checks:
  rows         non-blank sheet rows = candidates imported from them
  raw          every row has a raw capture holding exactly the bytes the row gives today
  fields       per column: filled cells = values stored + "?" cells stored as not recorded
  evaluations  every stored score is an evaluation under 2026-08-04 with the same score, tier and
               recommendation, and the replay of 2026-08-04 reads the same stored scores (A2)
  sample       random rows, sheet beside database, written for a person to check by hand

The sample holds candidate data, so it goes outside the repository and is written owner-only.
"""

import argparse
import csv
import io
import json
import os
import random
import sys
from datetime import date
from pathlib import Path
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Connection

from importer.rows import (
    CRITERIA_VERSION,
    FIELD_COLUMNS,
    QUESTION_MARK_COLUMNS,
    RAW_ONLY_COLUMNS,
    RECOMMENDATION,
    SCORE,
    SOURCE,
    TIER,
    PreparedRow,
    cell_text,
    is_blank,
    prepare_row,
)
from importer.tai_master import master_path
from jobs.queue import engine_from_environment
from replay.baseline import inside_git_repository, run_baseline
from replay.results import apply_rulings
from replay.rulings import RULINGS
from replay.workbook import MasterSheet, WorkbookError, read_master

# The date CI's parity job pins, so both read the same replay.
REPLAY_RUN_DATE = date(2026, 9, 14)
SAMPLE_COLUMNS = (
    "sheet_row",
    "column",
    "sheet_value",
    "stored_value",
    "stored_status",
    "inference",
    "agrees",
    "checked_by",
    "note",
)


def _prefix(source: str) -> dict[str, str]:
    return {"prefix": f"{source}:row:"}


_OWN_ROWS = "left(c.source_key, length(:prefix)) = :prefix"


def reconcile(conn: Connection, sheet: MasterSheet, source: str = SOURCE) -> dict[str, Any]:
    prepared = [prepare_row(sheet, row, source) for row in sheet.rows]
    total = len(prepared)

    candidates = conn.execute(
        text(f"SELECT count(*) FROM core.candidate c WHERE {_OWN_ROWS}"), _prefix(source)
    ).scalar_one()

    captures = {
        (row.external_id, bytes(row.content_sha256))
        for row in conn.execute(
            text(
                "SELECT external_id, content_sha256 FROM raw.capture "
                "WHERE source = :source AND left(external_id, 65) = :workbook"
            ),
            {"source": source, "workbook": f"{sheet.sha256}:"},
        )
    }
    raw_matching = sum((p.external_id, p.payload_sha256) in captures for p in prepared)

    stored_fields = {
        row.field: (row.stored_values, row.not_recorded)
        for row in conn.execute(
            text(
                f"""
                SELECT f.field, count(f.value) AS stored_values,
                       count(*) FILTER (WHERE f.verification_status = 'not_recorded')
                         AS not_recorded
                FROM core.candidate_field f JOIN core.candidate c ON c.id = f.candidate_id
                WHERE {_OWN_ROWS}
                GROUP BY f.field
                """
            ),
            _prefix(source),
        )
    }
    fields = []
    for column, field, _inference in FIELD_COLUMNS:
        cells = [row.values.get(column) for row in sheet.rows]
        filled = sum(not is_blank(value) for value in cells)
        question_marks = (
            sum(not is_blank(v) and cell_text(v).strip() == "?" for v in cells)
            if column in QUESTION_MARK_COLUMNS
            else 0
        )
        values, not_recorded = stored_fields.get(field, (0, 0))
        fields.append(
            {
                "column": column,
                "field": field,
                "sheet_filled": filled,
                "sheet_question_marks": question_marks,
                "stored_values": values,
                "stored_not_recorded": not_recorded,
                "match": values == filled - question_marks and values + not_recorded == total,
            }
        )
    raw_only = [
        {
            "column": column,
            "sheet_filled": sum(not is_blank(row.values.get(column)) for row in sheet.rows),
            "waiting_on": waiting_on,
        }
        for column, waiting_on in RAW_ONLY_COLUMNS.items()
    ]

    expected = {p.source_key: p.evaluation for p in prepared if p.evaluation is not None}
    stored_evaluations = conn.execute(
        text(
            f"""
            SELECT c.source_key, e.score, e.tier, e.recommendation
            FROM core.evaluation e JOIN core.candidate c ON c.id = e.candidate_id
            WHERE e.criteria_version_id = :version AND e.origin = 'stored' AND {_OWN_ROWS}
            """
        ),
        {"version": CRITERIA_VERSION, **_prefix(source)},
    ).all()
    same_as_sheet = 0
    for row in stored_evaluations:
        wanted = expected.get(row.source_key)
        if (
            wanted is not None
            and row.score == wanted.score
            and (row.tier, row.recommendation) == (wanted.tier, wanted.recommendation)
        ):
            same_as_sheet += 1
    sheet_scores = sum(not is_blank(row.values.get(SCORE)) for row in sheet.rows)

    results, _stale = apply_rulings(run_baseline(sheet, REPLAY_RUN_DATE), sheet.sha256, RULINGS)
    replay_scores = {
        f"{source}:row:{r.sheet_row}": r.stored.score for r in results if r.has_stored_score
    }
    replay_reads_the_same = replay_scores == {k: e.score for k, e in expected.items()}
    unexplained = sum(r.unexplained_difference for r in results)

    summary: dict[str, Any] = {
        "workbook_sha256": sheet.sha256,
        "rows": {"sheet": total, "candidates": candidates, "match": candidates == total},
        "raw_captures": {"rows": total, "matching": raw_matching, "match": raw_matching == total},
        "fields": fields,
        "raw_only_columns": raw_only,
        "evaluations": {
            "sheet_scores": sheet_scores,
            "whole_number_scores": len(expected),
            "stored": len(stored_evaluations),
            "same_as_sheet": same_as_sheet,
            "match": len(stored_evaluations) == len(expected) == same_as_sheet,
        },
        "replay": {
            "criteria_version": CRITERIA_VERSION,
            "run_date": REPLAY_RUN_DATE.isoformat(),
            "stored_scores": sum(r.has_stored_score for r in results),
            "score_matches": sum(r.score_match for r in results),
            "ruled_differences": sum(r.ruling is not None for r in results),
            "unexplained_differences": unexplained,
            "reads_the_same_stored_scores": replay_reads_the_same,
            "parity": unexplained == 0,
        },
    }
    summary["counts_match"] = (
        summary["rows"]["match"]
        and summary["raw_captures"]["match"]
        and all(f["match"] for f in fields)
        and summary["evaluations"]["match"]
        and replay_reads_the_same
    )
    return summary


def _sample_line(
    row: PreparedRow, column: str, sheet: str, stored: tuple[Any, ...] | None
) -> dict[str, Any]:
    value, status, inference = stored if stored is not None else ("", "missing", "")
    return {
        "sheet_row": row.sheet_row,
        "column": column,
        "sheet_value": sheet,
        "stored_value": "" if value is None else str(value),
        "stored_status": status,
        "inference": inference or "",
        "agrees": "",
        "checked_by": "",
        "note": "",
    }


def sample_rows(
    conn: Connection, sheet: MasterSheet, size: int, seed: int, source: str = SOURCE
) -> list[dict[str, Any]]:
    """Sheet beside database for random rows. "agrees" is what the tool expects; check by hand."""
    chosen = random.Random(seed).sample(list(sheet.rows), min(size, len(sheet.rows)))
    prepared = sorted(
        (prepare_row(sheet, row, source) for row in chosen), key=lambda p: p.sheet_row
    )
    keys = [p.source_key for p in prepared]
    fields = {
        (row.source_key, row.field): (row.value, row.verification_status, row.inference)
        for row in conn.execute(
            text(
                "SELECT c.source_key, f.field, f.value, f.verification_status, f.inference "
                "FROM core.candidate_field f JOIN core.candidate c ON c.id = f.candidate_id "
                "WHERE c.source_key = ANY(:keys)"
            ),
            {"keys": keys},
        )
    }
    evaluations = {
        row.source_key: row
        for row in conn.execute(
            text(
                "SELECT c.source_key, e.score, e.tier, e.recommendation "
                "FROM core.evaluation e JOIN core.candidate c ON c.id = e.candidate_id "
                "WHERE e.criteria_version_id = :version AND e.origin = 'stored' "
                "AND c.source_key = ANY(:keys)"
            ),
            {"version": CRITERIA_VERSION, "keys": keys},
        )
    }
    by_row = {row.sheet_row: row for row in chosen}

    lines: list[dict[str, Any]] = []
    for row in prepared:
        values = by_row[row.sheet_row].values
        for field in row.fields:
            sheet_value = (
                "" if is_blank(values.get(field.column)) else cell_text(values[field.column])
            )
            stored = fields.get((row.source_key, field.field))
            line = _sample_line(row, field.column, sheet_value, stored)
            line["agrees"] = (
                "yes" if stored == (field.value, field.status, field.inference) else "no"
            )
            lines.append(line)
        found = evaluations.get(row.source_key)
        wanted = row.evaluation
        for column, attribute in (
            (SCORE, "score"),
            (TIER, "tier"),
            (RECOMMENDATION, "recommendation"),
        ):
            sheet_value = "" if is_blank(values.get(column)) else cell_text(values[column])
            stored_value = None if found is None else getattr(found, attribute)
            status = "missing" if found is None else "stored evaluation"
            line = _sample_line(row, column, sheet_value, (stored_value, status, ""))
            expected = None if wanted is None else getattr(wanted, attribute)
            line["agrees"] = "yes" if stored_value == expected else "no"
            lines.append(line)
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
    lines = [
        "# TAI_Master import reconciliation",
        "",
        f"Workbook SHA-256 `{summary['workbook_sha256']}`. Counts only; no candidate values.",
        "",
        f"**Counts match: {mark(summary['counts_match'])}.** "
        f"The {sample_size}-row sample (seed {seed}) is signed off by hand, outside this report.",
        "",
        "## Rows, raw captures and evaluations",
        "",
        "| Check | Sheet | Database | Match |",
        "|---|---:|---:|---|",
        f"| Non-blank rows vs candidates | {rows['sheet']:,} | {rows['candidates']:,} "
        f"| {mark(rows['match'])} |",
        f"| Rows vs raw captures with identical bytes | {raw['rows']:,} | {raw['matching']:,} "
        f"| {mark(raw['match'])} |",
        f"| Whole-number stored scores vs evaluations under {replay['criteria_version']} "
        f"| {evaluations['whole_number_scores']:,} | {evaluations['stored']:,} "
        f"| {mark(evaluations['match'])} |",
        f"| Evaluations identical to the sheet (score, tier, recommendation) "
        f"| {evaluations['whole_number_scores']:,} | {evaluations['same_as_sheet']:,} "
        f"| {mark(evaluations['match'])} |",
        "",
        f"Score cells in the sheet: {evaluations['sheet_scores']:,}. Any that are not whole "
        "numbers are listed as unresolved in the import run, not imported.",
        "",
        "## Replay",
        "",
        f"Criteria {replay['criteria_version']} replayed as of {replay['run_date']}: "
        f"{replay['score_matches']:,} of {replay['stored_scores']:,} stored scores match exactly, "
        f"{replay['ruled_differences']:,} differences are ruled, "
        f"{replay['unexplained_differences']:,} unexplained. "
        f"Parity: {mark(replay['parity'])}. "
        f"The replay reads the same stored scores as the database holds: "
        f"{mark(replay['reads_the_same_stored_scores'])}.",
        "",
        "## Fields",
        "",
        '| Column | Field | Filled in sheet | "?" cells | Stored values | Not recorded | Match |',
        "|---|---|---:|---:|---:|---:|---|",
    ]
    for f in summary["fields"]:
        lines.append(
            f"| {f['column']} | `{f['field']}` | {f['sheet_filled']:,} "
            f"| {f['sheet_question_marks']:,} | {f['stored_values']:,} "
            f"| {f['stored_not_recorded']:,} | {mark(f['match'])} |"
        )
    lines += [
        "",
        "## Kept in the raw capture only",
        "",
        "Every cell of these columns is in the raw capture, which matched byte for byte above.",
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
    if inside_git_repository(out_dir):
        print(
            "error: --out is inside a git repository; the sample holds candidate data.",
            file=sys.stderr,
        )
        return 2
    try:
        master = master_path(args.master)
        sheet = read_master(master, sheet=args.sheet)
    except (ValueError, WorkbookError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    with engine_from_environment().connect() as conn:
        summary = reconcile(conn, sheet)
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

    disagreements = sum(line["agrees"] == "no" for line in lines)
    print(f"Counts match: {'yes' if summary['counts_match'] else 'NO'}.")
    print(f"Replay parity: {'yes' if summary['replay']['parity'] else 'no'}.")
    print(
        f"Sample: {len({line['sheet_row'] for line in lines})} rows, {disagreements} disagreements."
    )
    print(f"  report  {out_dir / (stem + '.md')}")
    print(f"  sample  {out_dir / (stem + '-sample.csv')}  (candidate data, never commit)")
    return 0 if summary["counts_match"] and disagreements == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
