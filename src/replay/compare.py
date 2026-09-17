"""Before new rules go live: who changes tier between two criteria versions (BR-304).

    python -m replay.compare --from 2026-08-04 --to 2026-10-01 --run-date 2026-09-14 \\
        --html artifacts/tier_moves_2026-10-01.html

Scores every master row with both registered versions and writes the who-moves-tier page:
counts from -> to and a sample of twenty sheet rows. Counts and sheet rows only, so the page may be
attached to the pull request that adds the version. The criteria owner signs the page before the
version is used by any opening.
"""

import argparse
import os
import sys
from datetime import date
from pathlib import Path

from replay.baseline import inside_git_repository
from replay.clock import pinned_today
from replay.mapping import map_row
from replay.tier_moves import Move, render_html
from replay.workbook import MasterSheet, WorkbookError, read_master
from scoring.versions import RULESETS


def compare(sheet: MasterSheet, before: str, after: str, run_date: date) -> list[Move]:
    old, new = RULESETS[before], RULESETS[after]
    moves: list[Move] = []
    with pinned_today(run_date):
        for row in sheet.rows:
            mapped = map_row(row)
            a = old.score_candidate(mapped.candidate, mode=mapped.mode)
            b = new.score_candidate(mapped.candidate, mode=mapped.mode)
            moves.append(Move(row.sheet_row, a.priority, b.priority))
    return moves


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m replay.compare")
    parser.add_argument("--from", dest="before", required=True, help="the version in force")
    parser.add_argument("--to", dest="after", required=True, help="the proposed version")
    parser.add_argument("--master", type=Path, default=os.environ.get("TALENT_MASTER_PATH"))
    parser.add_argument("--run-date", type=date.fromisoformat, default=date.today())
    parser.add_argument("--sheet")
    parser.add_argument("--html", type=Path, required=True, help="where to write the page")
    args = parser.parse_args(argv)

    for version in (args.before, args.after):
        if version not in RULESETS:
            known = ", ".join(sorted(RULESETS))
            print(f"error: criteria {version} is not registered. Known: {known}.", file=sys.stderr)
            return 2
    if args.master is None:
        print("error: set TALENT_MASTER_PATH or pass --master.", file=sys.stderr)
        return 2
    master = Path(args.master).expanduser()
    if inside_git_repository(master):
        print("error: the workbook is inside a git repository; move it out.", file=sys.stderr)
        return 2
    try:
        sheet = read_master(master, sheet=args.sheet)
    except WorkbookError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    moves = compare(sheet, args.before, args.after, args.run_date)
    moved = sum(m.before != m.after for m in moves)
    page = render_html(
        title=f"Criteria {args.after} against {args.before}",
        before_label=f"criteria {args.before}",
        after_label=f"criteria {args.after}",
        facts=[
            ("Workbook", sheet.file_name),
            ("Workbook SHA-256", sheet.sha256),
            ("Run date", args.run_date.isoformat()),
            ("In force", args.before),
            ("Proposed", args.after),
        ],
        moves=moves,
        verdict=(
            f"{moved:,} candidates change tier. The criteria owner signs this page before "
            f"criteria {args.after} is used by any opening."
            if moved
            else "Nobody changes tier."
        ),
        passed=moved == 0,
    )
    args.html.parent.mkdir(parents=True, exist_ok=True)
    args.html.write_text(page, encoding="utf-8")
    print(f"{moved:,} of {len(moves):,} rows change tier. Page: {args.html}")
    return 0


if __name__ == "__main__":
    if os.environ.get("PYTHONHASHSEED") != "0":
        environment = {**os.environ, "PYTHONHASHSEED": "0"}
        os.execve(
            sys.executable, [sys.executable, "-m", "replay.compare", *sys.argv[1:]], environment
        )
    sys.exit(main())
