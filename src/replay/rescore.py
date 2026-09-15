"""BR-704: candidates scored before the 3 August 2026 fixes, checked against today's rules.

    python -m replay.rescore --run-date 2026-09-14 [--master PATH]
        [--report-copy docs/migration/RESCORE_REPORT.md]

Replays criteria version 2026-08-04, which includes both fixes, over every row. For every row
the old matching could have disqualified, and for every row added before 3 August, it compares the
stored result with today's. A result that would change and has no ruling is a pending change: it is
listed by sheet row and never applied here. Applying one needs the criteria owner's sign-off.

The report names sheet rows and tiers only, never candidate values, so it may be committed. Exit
status 1 when a pending change exists.
"""

import argparse
import os
import sys
from collections import Counter
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

from replay.baseline import inside_git_repository, run_baseline
from replay.fixes import FIX_DATE, FIXES
from replay.mapping import text
from replay.results import RowResult, apply_rulings
from replay.rulings import RULINGS
from replay.workbook import MasterSheet, WorkbookError, read_master

ALREADY = "stored result already matches today's rules"
RULED = "differs, covered by a ruling"
PENDING_TIER = "tier would change: pending sign-off"
PENDING_SCORE = "score would change, same tier: pending sign-off"
NEVER_SCORED = "never scored"
OUTCOMES = (ALREADY, RULED, PENDING_TIER, PENDING_SCORE, NEVER_SCORED)
PENDING = (PENDING_TIER, PENDING_SCORE)


@dataclass(frozen=True, slots=True)
class CheckedRow:
    sheet_row: int
    fixes: tuple[str, ...]
    added_before_fix: bool | None
    outcome: str
    stored: str
    replayed: str


def classify(result: RowResult) -> str:
    if not result.has_stored_score:
        return NEVER_SCORED
    if result.score_match:
        return ALREADY
    if result.ruling is not None:
        return RULED
    return PENDING_TIER if result.stored.tier != result.replayed.tier else PENDING_SCORE


def _added(values: dict[str, Any]) -> date | None:
    try:
        return date.fromisoformat(text(values.get("Date Added"))[:10])
    except ValueError:
        return None


def check(sheet: MasterSheet, run_date: date) -> dict[str, Any]:
    results, _stale = apply_rulings(run_baseline(sheet, run_date), sheet.sha256, RULINGS)
    by_row = {result.sheet_row: result for result in results}

    checked: list[CheckedRow] = []
    before_fix: Counter[str] = Counter()
    for row in sheet.rows:
        result = by_row[row.sheet_row]
        added = _added(row.values)
        outcome = classify(result)
        if added is not None and added < FIX_DATE:
            before_fix[outcome] += 1
        fixes = tuple(fix.name for fix in FIXES if fix.caught(text(row.values.get(fix.column))))
        if fixes or outcome in PENDING or outcome == RULED:
            checked.append(
                CheckedRow(
                    sheet_row=row.sheet_row,
                    fixes=fixes,
                    added_before_fix=None if added is None else added < FIX_DATE,
                    outcome=outcome,
                    stored=f"{result.stored.score} {result.stored.tier}".strip(),
                    replayed=f"{result.replayed.score} {result.replayed.tier}",
                )
            )

    per_fix = {
        fix.name: {
            "column": fix.column,
            "old_rule": fix.old_rule,
            "rows": sum(fix.name in c.fixes for c in checked),
            **{
                outcome: sum(fix.name in c.fixes and c.outcome == outcome for c in checked)
                for outcome in OUTCOMES
            },
        }
        for fix in FIXES
    }
    pending = [c for c in checked if c.outcome in PENDING]
    return {
        "workbook_sha256": sheet.sha256,
        "run_date": run_date.isoformat(),
        "fix_date": FIX_DATE.isoformat(),
        "rows": len(sheet.rows),
        "per_fix": per_fix,
        "added_before_fix": {outcome: before_fix[outcome] for outcome in OUTCOMES},
        "ruled": [c for c in checked if c.outcome == RULED],
        "pending": pending,
    }


def render_report(summary: dict[str, Any]) -> str:
    pending, ruled = summary["pending"], summary["ruled"]
    lines = [
        "# Re-scoring check for the 3 August 2026 fixes (BR-704)",
        "",
        f"Workbook SHA-256 `{summary['workbook_sha256']}`. Criteria 2026-08-04 replayed as of "
        f"{summary['run_date']}. Sheet rows and tiers only; no candidate values.",
        "",
    ]
    if pending:
        lines += [
            f"**Pending changes: {len(pending)}.** Each is listed below. None is applied until the "
            "criteria owner signs off.",
            "",
        ]
    else:
        lines += [
            "**Pending changes: 0.** Nothing needs re-scoring: every stored result the fixes could "
            "have changed already matches today's rules, or is covered by a ruling.",
            "",
        ]

    lines += [
        "## The fixes",
        "",
        "| Fix | Column | What the old rule did | Rows it could have disqualified | Already match "
        "today | Ruled | Pending |",
        "|---|---|---|---:|---:|---:|---:|",
    ]
    for name, fix in summary["per_fix"].items():
        pending_count = fix[PENDING_TIER] + fix[PENDING_SCORE]
        lines.append(
            f"| {name} | {fix['column']} | {fix['old_rule']} | {fix['rows']:,} | {fix[ALREADY]:,} "
            f"| {fix[RULED]:,} | {pending_count:,} |"
        )

    lines += [
        "",
        f"## Rows added before {summary['fix_date']}",
        "",
        "| Outcome | Rows |",
        "|---|---:|",
    ]
    lines += [
        f"| {outcome} | {count:,} |" for outcome, count in summary["added_before_fix"].items()
    ]

    for title, rows in (("Rows covered by a ruling", ruled), ("Pending changes", pending)):
        lines += ["", f"## {title}", ""]
        if not rows:
            lines += ["None."]
            continue
        lines += ["| Sheet row | Fix | Stored | Today | Outcome |", "|---:|---|---|---|---|"]
        lines += [
            f"| {c.sheet_row} | {', '.join(c.fixes) or 'none'} | {c.stored} | {c.replayed} "
            f"| {c.outcome} |"
            for c in rows
        ]

    lines += [
        "",
        "## Sign-off",
        "",
        "BR-704 asks for tier changes to be reported before they are applied. The criteria owner "
        "records acceptance of this report in docs/migration/WEEK3_DECISIONS.md.",
        "",
        "## Reproduce",
        "",
        "```bash",
        f'python -m replay.rescore --master "$TALENT_MASTER_PATH" --run-date {summary["run_date"]}',
        "```",
        "",
    ]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m replay.rescore", description=__doc__.splitlines()[0]
    )
    parser.add_argument("--master", type=Path, default=os.environ.get("TALENT_MASTER_PATH"))
    parser.add_argument("--run-date", type=date.fromisoformat, default=date(2026, 9, 14))
    parser.add_argument("--sheet", help="sheet name (default: the first sheet)")
    parser.add_argument(
        "--report-copy", type=Path, help="counts and sheet rows only, so it may be committed"
    )
    args = parser.parse_args(argv)

    if args.master is None:
        print("error: set TALENT_MASTER_PATH or pass --master.", file=sys.stderr)
        return 2
    master = Path(args.master).expanduser()
    if inside_git_repository(master):
        print(
            f"error: {master.name} is inside a git repository. "
            "Candidate data must live outside it.",
            file=sys.stderr,
        )
        return 2
    try:
        sheet = read_master(master, sheet=args.sheet)
    except WorkbookError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    summary = check(sheet, args.run_date)
    report = render_report(summary)
    if args.report_copy is not None:
        args.report_copy.parent.mkdir(parents=True, exist_ok=True)
        args.report_copy.write_text(report, encoding="utf-8")
    else:
        print(report)
    print(
        f"Rows the fixes could have changed: "
        f"{', '.join(f'{name} {fix["rows"]}' for name, fix in summary['per_fix'].items())}. "
        f"Ruled: {len(summary['ruled'])}. Pending: {len(summary['pending'])}.",
        file=sys.stderr,
    )
    return 1 if summary["pending"] else 0


if __name__ == "__main__":
    if os.environ.get("PYTHONHASHSEED") != "0":
        # The ruleset iterates over sets; a fixed hash seed keeps every run's text identical.
        os.execve(
            sys.executable,
            [sys.executable, "-m", "replay.rescore", *sys.argv[1:]],
            {**os.environ, "PYTHONHASHSEED": "0"},
        )
    sys.exit(main())
