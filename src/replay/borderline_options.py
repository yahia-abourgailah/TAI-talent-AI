"""What each borderline rule would cost, measured on the master workbook (BR-310). Counts only.

    python -m replay.borderline_options --run-date 2026-09-14 \\
        --report-copy docs/criteria/BORDERLINE_OPTIONS.md

The criteria owner picks the rule; this puts the size of each option's queue in front of them.
Three kinds of option are measured over every replayed candidate the gates did not disqualify:

    band        within N points of a tier line, either side
    below_line  1 to N points under a tier line
    input flip  the tier changes when one guessed input is read the other way: the age one year
                either side, no age at all, or the location matched as an unknown place

Each track is measured against its own lines. No per-row output is written, so nothing here holds
candidate data, and the report may be committed.
"""

import argparse
import os
import sys
from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from datetime import date
from pathlib import Path

from replay.baseline import inside_git_repository
from replay.clock import pinned_today
from replay.mapping import map_row
from replay.workbook import MasterSheet, WorkbookError, read_master
from scoring.borderline import RULES, Policy, lines_for, near_line
from scoring.rulesets import v2026_08_04 as ruleset
from scoring.rulesets.v2026_08_04 import Candidate

CRITERIA_VERSION = "2026-08-04"
BAND_POINTS = (1, 2, 3, 5)
NEAR_LINE = 5  # the score table shows this many points either side of each line
UNKNOWN_PLACE = "zzz unmatched place"  # matches no Cairo, outside or country list

Flip = Callable[[Candidate], Candidate | None]

FLIPS: dict[str, Flip] = {
    "age one year younger": lambda c: None if c.age is None else replace(c, age=c.age - 1),
    "age one year older": lambda c: None if c.age is None else replace(c, age=c.age + 1),
    "no age": lambda c: None if c.age is None else replace(c, age=None),
    "location read as the other Cairo band": lambda c: _other_cairo_band(c),
    "location read as an unknown place": lambda c: (
        replace(c, location=UNKNOWN_PLACE) if c.location.strip() else None
    ),
}


def _location_points(c: Candidate) -> int:
    return ruleset._score_location(c)[0]


def _other_cairo_band(c: Candidate) -> Candidate | None:
    """Near New Cairo (30) read as farther Cairo (15), or the other way round. A place is chosen
    from the ruleset's own list that scores the other band and trips no gate."""
    points = _location_points(c)
    if points not in (30, 15):
        return None
    places = ruleset.OTHER_CAIRO if points == 30 else ruleset.NEAR_NEW_CAIRO
    want = 15 if points == 30 else 30
    for place in sorted(places):
        changed = replace(c, location=place)
        if _location_points(changed) == want and not ruleset._check_hard_disqualifiers(changed)[0]:
            return changed
    return None


@dataclass(frozen=True, slots=True)
class Scored:
    mode: str
    score: int
    tier: str
    disqualified: bool
    flipped: dict[str, tuple[str, bool]]  # flip -> (tier, disqualified)


def score_sheet(sheet: MasterSheet, run_date: date) -> list[Scored]:
    out: list[Scored] = []
    with pinned_today(run_date):
        for row in sheet.rows:
            mapped = map_row(row)
            result = ruleset.score_candidate(mapped.candidate, mode=mapped.mode)
            flipped: dict[str, tuple[str, bool]] = {}
            for name, flip in FLIPS.items():
                changed = flip(mapped.candidate)
                if changed is not None:
                    other = ruleset.score_candidate(changed, mode=mapped.mode)
                    flipped[name] = (other.priority, other.disqualified)
            out.append(
                Scored(
                    mode=mapped.mode,
                    score=result.overall_score,
                    tier=result.priority,
                    disqualified=result.disqualified,
                    flipped=flipped,
                )
            )
    return out


def _pct(part: int, whole: int) -> str:
    return f"{part / whole:.1%}" if whole else "n/a"


def _table(header: Sequence[str], rows: Sequence[Sequence[object]]) -> list[str]:
    lines = ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
    lines += ["| " + " | ".join(str(cell) for cell in row) + " |" for row in rows]
    return [*lines, ""]


def _count(scored: Sequence[Scored], policy: Policy) -> int:
    return sum(near_line(s.score, CRITERIA_VERSION, s.mode, policy) is not None for s in scored)


def render(sheet: MasterSheet, run_date: date, results: Sequence[Scored]) -> str:
    passed = [s for s in results if not s.disqualified]
    by_mode = {mode: [s for s in passed if s.mode == mode] for mode in ("entry", "headhunt")}
    names = {"entry": "Track A (P tiers)", "headhunt": "Track B (T tiers)"}
    total = len(passed)

    out = [
        f"# Borderline options: criteria version {CRITERIA_VERSION}",
        "",
        "Generated by `python -m replay.borderline_options`. Counts only. For the criteria owner "
        "to choose how close to a tier line is borderline (BR-310).",
        "",
    ]
    out += _table(
        ("Run", "Value"),
        [
            ("Workbook", sheet.file_name),
            ("Workbook SHA-256", f"`{sheet.sha256}`"),
            ("Run date (the ruleset's today)", run_date.isoformat()),
            ("Rows replayed", f"{len(results):,}"),
            ("Not disqualified by a gate", f"{total:,}"),
            *((f"  of which {names[m]}", f"{len(group):,}") for m, group in by_mode.items()),
        ],
    )

    out += [
        "## Option 1 and 2: a band of points",
        "",
        "`band N`: within N points of a line, either side, the line included. "
        "`below N`: 1 to N points under a line. Share is of the candidates not disqualified.",
        "",
    ]
    rows = []
    for points in BAND_POINTS:
        for rule in RULES:
            policy = Policy(rule, points)
            counts = [_count(group, policy) for group in by_mode.values()]
            label = f"±{points}" if rule == "band" else f"below {points}"
            rows.append(
                (label, *(f"{c:,}" for c in counts), f"{sum(counts):,}", _pct(sum(counts), total))
            )
    out += _table(("Rule", *names.values(), "Queue", "Share"), rows)

    out += [
        "## Candidates at each score near a line",
        "",
        "The scorer gives lumps, not a smooth spread: a single score can hold hundreds of people.",
        "",
    ]
    for mode, group in by_mode.items():
        scores = Counter(s.score for s in group)
        table = []
        for line, above, _below in lines_for(CRITERIA_VERSION, mode):
            for score in range(line - NEAR_LINE, line + NEAR_LINE + 1):
                if scores[score]:
                    offset = score - line
                    where = (
                        f"the {above} line"
                        if offset == 0
                        else f"{abs(offset)} {'under' if offset < 0 else 'over'} the {above} line"
                    )
                    table.append((score, f"{scores[score]:,}", where))
        out += [f"### {names[mode]}", ""]
        out += _table(("Score", "Candidates", "Where it sits"), table)

    out += [
        "## Option 3: a guessed input read the other way",
        "",
        "Each flip re-scores the candidate with one input changed. A candidate counts when the "
        "tier changes. Candidates without the input (no age, no location) cannot flip on it.",
        "",
    ]
    any_flip: set[int] = set()
    rows = []
    moves: Counter[str] = Counter()
    for name in FLIPS:
        if name == "location read as an unknown place":
            continue  # reported below on its own: it is the bound, not an option
        eligible = changed = gate = 0
        for index, s in enumerate(passed):
            if name not in s.flipped:
                continue
            eligible += 1
            tier, disqualified = s.flipped[name]
            if disqualified:
                gate += 1
            if tier != s.tier:
                changed += 1
                any_flip.add(index)
                moves[f"{s.tier} → {tier}"] += 1
        rows.append((name, f"{eligible:,}", f"{changed:,}", f"{gate:,}", _pct(changed, total)))
    out += _table(
        ("Flip", "Candidates it applies to", "Tier changes", "Now disqualified", "Share of all"),
        rows,
    )
    unknown = "location read as an unknown place"
    lost = sum(1 for s in passed if unknown in s.flipped and s.flipped[unknown][0] != s.tier)
    out += [
        f"**Any flip changes the tier: {len(any_flip):,} candidates "
        f"({_pct(len(any_flip), total)}).**",
        "",
        f"For scale, not as an option: if no location matched at all (every place read as "
        f"unknown, 5 points), {lost:,} candidates ({_pct(lost, total)}) would change tier. "
        "Location is worth 30, 15 or 5 points, which is why so much hangs on it.",
        "",
    ]
    out += _table(
        ("Tier move (any flip)", "Times"),
        [(move, f"{moves[move]:,}") for move in sorted(moves)],
    )
    out += [
        "The workbook has no graduation year, so an age inferred from graduation cannot be "
        "flipped on this data; new applications from CVs can carry one.",
        "",
        "## Reproduce",
        "",
        "```bash",
        "python -m replay.borderline_options --run-date " + run_date.isoformat(),
        "```",
        "",
    ]
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m replay.borderline_options")
    parser.add_argument("--master", type=Path, default=os.environ.get("TALENT_MASTER_PATH"))
    parser.add_argument("--run-date", type=date.fromisoformat, default=date.today())
    parser.add_argument("--sheet")
    parser.add_argument("--report-copy", type=Path, help="write the counts-only report here")
    args = parser.parse_args(argv)
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
    report = render(sheet, args.run_date, score_sheet(sheet, args.run_date))
    if args.report_copy is not None:
        args.report_copy.parent.mkdir(parents=True, exist_ok=True)
        args.report_copy.write_text(report, encoding="utf-8")
    print(report)
    return 0


if __name__ == "__main__":
    sys.exit(main())
