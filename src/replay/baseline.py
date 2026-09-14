"""Week 1 baseline: replay criteria version 2026-08-04 over every master row and record the result.

    python -m replay.baseline --master "$TALENT_MASTER_PATH" --out ~/TAI-data/baseline \\
        --run-date 2026-09-14 [--report-copy docs/migration/BASELINE_REPORT.md]

Writes, into a directory that must be outside any git repository:
    baseline-<date>.jsonl   one line per row: stored and replayed score, tier, flags, signals
    baseline-<date>.json    run metadata and aggregate counts
    report-<date>.md        the aggregate report, counts only

The per-row file holds candidate data and is written owner-only. The same workbook, ruleset and
run date always produce byte-identical files, so a week 2 replay can be compared line by line.
"""

import argparse
import json
import os
import sys
from collections.abc import Sequence
from dataclasses import asdict
from datetime import date
from pathlib import Path
from typing import Any

from replay.clock import pinned_today
from replay.mapping import map_row, number, split_items, text
from replay.report import likely_cause, render_report, summarise
from replay.results import Outcome, RowResult, apply_rulings
from replay.rulings import RULINGS, Ruling
from replay.workbook import MasterSheet, WorkbookError, read_master
from scoring.rulesets import v2026_08_04 as ruleset

RULESET_VERSION = "2026-08-04"
TOOL_VERSION = 1


def _stored_outcome(values: dict[str, Any]) -> Outcome:
    score = number(values.get("Score"), "Score", [])
    return Outcome(
        score=None if score is None else int(score),
        tier=text(values.get("Tier")),
        recommendation=text(values.get("Recommendation")),
        flags=split_items(values.get("Flags (reasons of disqualification)")),
        signals=split_items(values.get("Signals (Reasons to call)")),
    )


def run_baseline(sheet: MasterSheet, run_date: date) -> list[RowResult]:
    mapped = [(row, map_row(row)) for row in sheet.rows]
    results: list[RowResult] = []
    with pinned_today(run_date):
        for row, candidate in mapped:
            scored = ruleset.score_candidate(candidate.candidate, mode=candidate.mode)
            results.append(
                RowResult(
                    sheet_row=row.sheet_row,
                    track=candidate.mode,
                    input_sha256=candidate.input_sha256,
                    parse_issues=candidate.parse_issues,
                    stored=_stored_outcome(row.values),
                    # The ruleset collects signals by iterating over sets, so their order
                    # changes between Python processes. Sorted, the file is reproducible.
                    replayed=Outcome(
                        score=scored.overall_score,
                        tier=scored.priority,
                        recommendation=scored.recommendation,
                        flags=tuple(sorted(scored.red_flags)),
                        signals=tuple(sorted(scored.key_signals)),
                    ),
                    replayed_disqualified=scored.disqualified,
                )
            )
    return results


def inside_git_repository(path: Path) -> bool:
    resolved = path.expanduser().resolve()
    return any((parent / ".git").exists() for parent in (resolved, *resolved.parents))


def _write_private(path: Path, content: str) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        handle.write(content)
    os.chmod(path, 0o600)  # the mode above is ignored when the file already existed


def _row_record(result: RowResult) -> dict[str, Any]:
    return {
        "sheet_row": result.sheet_row,
        "track": result.track,
        "input_sha256": result.input_sha256,
        "parse_issues": list(result.parse_issues),
        "stored": asdict(result.stored),
        "replayed": asdict(result.replayed),
        "replayed_disqualified": result.replayed_disqualified,
        "score_match": result.score_match,
        "tier_match": result.tier_match,
        "ruling": None if result.ruling is None else asdict(result.ruling),
        "likely_cause": likely_cause(result) if result.unexplained_difference else None,
    }


def write_outputs(
    out_dir: Path,
    sheet: MasterSheet,
    run_date: date,
    results: list[RowResult],
    stale_rulings: Sequence[Ruling] = (),
) -> tuple[dict[str, Path], dict[str, Any], str]:
    out_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    meta: dict[str, Any] = {
        "tool_version": TOOL_VERSION,
        "ruleset": RULESET_VERSION,
        "run_date": run_date.isoformat(),
        "workbook": {
            "file": sheet.file_name,
            "sha256": sheet.sha256,
            "sheet": sheet.sheet_name,
            "sheets": list(sheet.sheet_names),
        },
        "rows": len(sheet.rows),
        "blank_rows_skipped": sheet.blank_rows_skipped,
        "python_hash_seed": os.environ.get("PYTHONHASHSEED"),
    }
    urls = [text(row.values.get("Profile URL")) for row in sheet.rows]
    summary = summarise(results, urls, stale_rulings)
    report = render_report(meta, summary)

    day = run_date.isoformat()
    paths = {
        "rows": out_dir / f"baseline-{day}.jsonl",
        "summary": out_dir / f"baseline-{day}.json",
        "report": out_dir / f"report-{day}.md",
    }
    lines = (json.dumps(_row_record(r), sort_keys=True, ensure_ascii=False) for r in results)
    _write_private(paths["rows"], "\n".join(lines) + "\n")
    _write_private(
        paths["summary"],
        json.dumps({**meta, "summary": summary}, indent=2, ensure_ascii=False) + "\n",
    )
    _write_private(paths["report"], report)
    return paths, summary, report


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m replay.baseline",
        description="Replay criteria version 2026-08-04 over the master workbook (BR-702).",
    )
    parser.add_argument(
        "--master",
        type=Path,
        default=os.environ.get("TALENT_MASTER_PATH"),
        help="the master workbook (default: $TALENT_MASTER_PATH)",
    )
    parser.add_argument(
        "--out", type=Path, required=True, help="output directory, outside any git repository"
    )
    parser.add_argument(
        "--run-date",
        type=date.fromisoformat,
        default=date.today(),
        help="the date the ruleset treats as today, YYYY-MM-DD (default: today)",
    )
    parser.add_argument("--sheet", help="sheet name (default: the first sheet)")
    parser.add_argument(
        "--report-copy",
        type=Path,
        help="also write the aggregate report here; it holds counts only, so it may be committed",
    )
    parser.add_argument(
        "--require-parity",
        action="store_true",
        help="exit with status 1 unless every difference is covered by a ruling (for CI)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.master is None:
        print("error: set TALENT_MASTER_PATH or pass --master.", file=sys.stderr)
        return 2
    master = Path(args.master).expanduser()
    out_dir = Path(args.out).expanduser()

    if inside_git_repository(master):
        print(
            f"error: {master.name} is inside a git repository. Candidate data must live outside "
            "it (docs/DATA_HANDLING.md); move the workbook and point --master at the new place.",
            file=sys.stderr,
        )
        return 2
    if inside_git_repository(out_dir):
        print(
            "error: --out is inside a git repository. The per-row file holds candidate data; "
            "choose a directory outside the repository.",
            file=sys.stderr,
        )
        return 2

    try:
        sheet = read_master(master, sheet=args.sheet)
    except WorkbookError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    results, stale = apply_rulings(run_baseline(sheet, args.run_date), sheet.sha256, RULINGS)
    paths, summary, report = write_outputs(out_dir, sheet, args.run_date, results, stale)
    if args.report_copy is not None:
        args.report_copy.parent.mkdir(parents=True, exist_ok=True)
        args.report_copy.write_text(report, encoding="utf-8")

    scored = summary["stored_scores"]
    print(
        f"Replayed {summary['rows']:,} rows with criteria {RULESET_VERSION} as of "
        f"{args.run_date.isoformat()}. Exact score match {summary['score_matches']:,}/{scored:,}, "
        f"tier match {summary['tier_matches']:,}/{scored:,}. "
        f"Ruled {summary['ruled_differences']:,}, "
        f"unexplained {summary['unexplained_differences']:,}."
    )
    for label, path in paths.items():
        print(f"  {label:<8} {path}")
    if args.require_parity and not summary["parity"]:
        print(
            "error: parity not reached; the report lists each unexplained difference.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    if os.environ.get("PYTHONHASHSEED") != "0":
        # The ruleset iterates over sets and some signals name the first keyword it meets, so the
        # text can change between processes. A fixed hash seed makes every run reproducible.
        environment = {**os.environ, "PYTHONHASHSEED": "0"}
        os.execve(
            sys.executable, [sys.executable, "-m", "replay.baseline", *sys.argv[1:]], environment
        )
    sys.exit(main())
