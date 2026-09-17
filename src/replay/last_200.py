"""The most recent candidates, scored by the old laptop script and by the platform (week 7).

    python -m replay.last_200 --legacy-script /path/outside/repo/scorer.py \\
        --out ~/TAI-data/last-200 --report-copy docs/migration/LAST_200_REPORT.md

The week 2 parity compared the platform with the scores the old runs stored. This compares it with
the old script running today, on the newest rows. Both sides get the same run date.

    legacy    the laptop script, loaded from its file as it is. It must offer the same
              `Candidate` and `score_candidate(candidate, mode)` the port was taken from.
    platform  what the scoring worker does: the row's values stored as text fields, read back with
              scoring.platform.candidate_from_fields, and scored with the registered version.

Every difference is listed with the part of the score that moved and the input behind it. A
difference is never fixed by changing the scorer: the criteria owner rules, the ruling is recorded,
and only then does anything change, as a new criteria version.

The per-row file is kept with the other replay outputs, outside the repository. Like the report,
it holds sheet rows, scores, tiers and field names only.
"""

import argparse
import importlib.util
import json
import os
import sys
from dataclasses import asdict, dataclass, fields
from datetime import date, datetime
from pathlib import Path
from types import ModuleType
from typing import Any

from replay.baseline import inside_git_repository
from replay.clock import pinned_today
from replay.mapping import track
from replay.workbook import MasterRow, MasterSheet, WorkbookError, file_sha256, read_master
from scoring.platform import FIELD_TO_COLUMN, candidate_from_fields
from scoring.versions import RULESETS

CRITERIA_VERSION = "2026-08-04"
DEFAULT_COUNT = 200

# The part of the score -> the input it reads. Compared in this order; the first that differs is
# named as the cause.
COMPONENTS: tuple[tuple[str, str], ...] = (
    ("disqualified", "a gate: title, location, age, experience or employer"),
    ("location_score", "location"),
    ("sales_fit_score", "title"),
    ("entry_level_score", "years of experience, age"),
    ("education_score", "education"),
    ("contact_score", "phone, email, profile link"),
    ("competitor_bonus", "employer"),
    ("platform_adjustment", "source platform"),
)


def load_legacy(path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location("legacy_scorer", path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load {path.name}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    for name in ("Candidate", "score_candidate"):
        if not hasattr(module, name):
            raise ImportError(f"{path.name} has no {name}")
    return module


def _added(row: MasterRow) -> datetime | None:
    value = row.values.get("Date Added")
    if isinstance(value, datetime):
        return value
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day)
    if isinstance(value, str) and value.strip():
        for parse in (datetime.fromisoformat, lambda v: datetime.strptime(v, "%d/%m/%Y")):
            try:
                return parse(value.strip())
            except ValueError:
                continue
    return None


def most_recent(sheet: MasterSheet, count: int) -> tuple[list[MasterRow], int]:
    """The newest rows by Date Added, then by sheet row (later rows were added later). Also the
    number of rows with no readable date, which sort as oldest."""
    undated = sum(_added(row) is None for row in sheet.rows)
    ranked = sorted(
        sheet.rows,
        key=lambda row: (_added(row) or datetime.min, row.sheet_row),
        reverse=True,
    )
    return ranked[:count], undated


def platform_fields(row: MasterRow) -> dict[str, str | None]:
    """The row as the platform stores it: one text value per field, blanks not recorded."""
    out: dict[str, str | None] = {}
    for field, column in FIELD_TO_COLUMN.items():
        value = row.values.get(column)
        text = None if value is None else str(value).strip()
        out[field] = text or None
    return out


@dataclass(frozen=True, slots=True)
class Pair:
    sheet_row: int
    track: str
    legacy_score: int
    legacy_tier: str
    platform_score: int
    platform_tier: str
    cause: str | None  # None when the two agree

    @property
    def differs(self) -> bool:
        return self.cause is not None


def _cause(legacy: Any, platform: Any) -> str | None:
    if (legacy.overall_score, legacy.priority) == (platform.overall_score, platform.priority) and (
        sorted(legacy.red_flags) == sorted(platform.red_flags)
    ):
        return None
    if legacy.track == "headhunt" or platform.track == "headhunt":
        # Track B results carry no component scores.
        if legacy.disqualified != platform.disqualified:
            return COMPONENTS[0][1]
        return "the Track B score (title, employer, tenure)"
    for name, field in COMPONENTS:
        if getattr(legacy, name, None) != getattr(platform, name, None):
            return field
    if legacy.overall_score != platform.overall_score:
        return "a bonus or penalty outside the listed parts (see signals)"
    return "the flags or signals only; score and tier agree"


def score_both(rows: list[MasterRow], legacy: ModuleType, run_date: date) -> list[Pair]:
    ruleset = RULESETS[CRITERIA_VERSION]
    legacy_fields = {f.name for f in fields(legacy.Candidate)}
    pairs = []
    with pinned_today(run_date):
        for row in rows:
            mode = track(row.values)
            candidate, _ = candidate_from_fields(platform_fields(row))
            platform = ruleset.score_candidate(candidate, mode=mode)
            given = {k: v for k, v in asdict(candidate).items() if k in legacy_fields}
            old = legacy.score_candidate(legacy.Candidate(**given), mode=mode)
            pairs.append(
                Pair(
                    sheet_row=row.sheet_row,
                    track=mode,
                    legacy_score=int(old.overall_score),
                    legacy_tier=str(old.priority),
                    platform_score=int(platform.overall_score),
                    platform_tier=str(platform.priority),
                    cause=_cause(old, platform),
                )
            )
    return pairs


def render(
    sheet: MasterSheet, legacy_sha256: str, run_date: date, pairs: list[Pair], undated: int
) -> str:
    different = [p for p in pairs if p.differs]
    tier_moves = [p for p in different if p.legacy_tier != p.platform_tier]
    out = [
        "# The last 200: the old script against the platform",
        "",
        "Generated by `python -m replay.last_200`. Sheet rows, scores, tiers and field names only.",
        "",
        "| Run | Value |",
        "|---|---|",
        f"| Workbook | {sheet.file_name} |",
        f"| Workbook SHA-256 | `{sheet.sha256}` |",
        f"| Old script SHA-256 | `{legacy_sha256}` |",
        f"| Platform criteria | {CRITERIA_VERSION} |",
        f"| Run date (both sides) | {run_date.isoformat()} |",
        f"| Rows compared | {len(pairs):,}, newest by Date Added "
        f"({undated:,} rows in the sheet have no readable date and count as oldest) |",
        "",
        "## Headline",
        "",
        f"- Same score, tier and flags: **{len(pairs) - len(different):,}**",
        f"- Different: **{len(different):,}**, of which a different tier: **{len(tier_moves):,}**",
        "",
        (
            "**No differences.** The platform scores the newest candidates exactly as the old "
            "script does today."
            if not different
            else "**Differences to rule on.** Each row below goes to the criteria owner. Nothing "
            "in the scorer changes until the ruling is recorded in src/replay/rulings.py."
        ),
        "",
    ]
    if different:
        out += [
            "## Every difference",
            "",
            "| Sheet row | Track | Old script | Platform | What moved |",
            "|---|---|---|---|---|",
        ]
        out += [
            f"| {p.sheet_row} | {'A' if p.track == 'entry' else 'B'} | "
            f"{p.legacy_score} {p.legacy_tier} | {p.platform_score} {p.platform_tier} | {p.cause} |"
            for p in sorted(different, key=lambda p: p.sheet_row)
        ]
        out.append("")
    out += [
        "## Reproduce",
        "",
        "```bash",
        "python -m replay.last_200 --legacy-script <the laptop scorer, outside the repository> "
        f"--out <directory outside the repository> --run-date {run_date.isoformat()}",
        "```",
        "",
    ]
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m replay.last_200")
    parser.add_argument("--legacy-script", type=Path, required=True)
    parser.add_argument("--master", type=Path, default=os.environ.get("TALENT_MASTER_PATH"))
    parser.add_argument("--out", type=Path, required=True, help="outside any git repository")
    parser.add_argument("--run-date", type=date.fromisoformat, default=date.today())
    parser.add_argument("--count", type=int, default=DEFAULT_COUNT)
    parser.add_argument("--sheet")
    parser.add_argument("--report-copy", type=Path)
    args = parser.parse_args(argv)

    if args.master is None:
        print("error: set TALENT_MASTER_PATH or pass --master.", file=sys.stderr)
        return 2
    master, out_dir = Path(args.master).expanduser(), Path(args.out).expanduser()
    for label, path in (("the workbook", master), ("--out", out_dir)):
        if inside_git_repository(path):
            print(f"error: {label} is inside a git repository.", file=sys.stderr)
            return 2
    if not args.legacy_script.is_file():
        print(f"error: no script at {args.legacy_script}.", file=sys.stderr)
        return 2
    try:
        legacy = load_legacy(args.legacy_script)
        sheet = read_master(master, sheet=args.sheet)
    except (ImportError, WorkbookError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    rows, undated = most_recent(sheet, args.count)
    pairs = score_both(rows, legacy, args.run_date)
    report = render(sheet, file_sha256(args.legacy_script), args.run_date, pairs, undated)

    out_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    day = args.run_date.isoformat()
    (out_dir / f"last-200-{day}.jsonl").write_text(
        "".join(json.dumps(asdict(p), sort_keys=True) + "\n" for p in pairs), encoding="utf-8"
    )
    (out_dir / f"last-200-{day}.md").write_text(report, encoding="utf-8")
    if args.report_copy is not None:
        args.report_copy.parent.mkdir(parents=True, exist_ok=True)
        args.report_copy.write_text(report, encoding="utf-8")
    different = sum(p.differs for p in pairs)
    print(f"Compared {len(pairs):,} rows: {different:,} differ. Report: {out_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
