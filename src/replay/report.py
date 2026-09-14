"""Aggregates a baseline run. Counts only: no candidate value can reach this output.

Signals and flags are reduced to their fixed wording ("Near New Cairo: <place>" becomes
"Near New Cairo"), and any wording seen in fewer than MIN_CATEGORY_ROWS rows is pooled, so rare
hand-typed notes in the legacy flags never surface.
"""

import re
from collections import Counter
from collections.abc import Sequence
from typing import Any

from replay.results import Outcome, RowResult

MIN_CATEGORY_ROWS = 10

DELTA_BANDS = ("-20+", "-10-19", "-5-9", "-1-4", "same", "+1-4", "+5-9", "+10-19", "+20+")
TIERS = ("P1", "P2", "P3", "P4", "T1", "T2", "T3", "T4")

SALES_KEYWORD = "Sales keyword"
SIGNAL_GROUPS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("Recency differs (last-active date)", ("Active recently", "Active within", "Profile stale")),
    ("Competitor bonus differs", ("Competitor brokerage", "Rehire candidate")),
    (
        "Experience or graduation differs",
        (
            "Recent grad",
            "Fresh graduate",
            "Final-year student",
            "No experience",
            "# year",
            "Too much experience",
        ),
    ),
    ("Location differs", ("Near New Cairo", "Cairo area")),
    ("Education differs", ("Bachelor's degree", "Undergraduate", "Diploma", "High school")),
    (
        "Contact differs",
        ("Egyptian phone", "Email or partial", "Profile URL only", "No contact info"),
    ),
    ("Profile quality differs", ("Unverifiable profile", "No profile photo", "Very low connect")),
    ("Other adjustment differs", ("Open to Work", "TikTok source", "Remote-only")),
)

_DQ_PREFIX = re.compile(r"^DISQUALIFIED:\s*")
_NUMBER = re.compile(r"\d+(?:\.\d+)?")


def category(item: str) -> str:
    """The fixed wording of a signal or flag, with the candidate-specific part removed."""
    wording = _DQ_PREFIX.sub("", item.strip())
    head = re.split(r"[:(]", wording, maxsplit=1)[0]
    head = _NUMBER.sub("#", head).strip(" -'\"\u2013\u2014\u2605\u26a0")  # dashes, star, warning
    return head[:48] or "(empty)"


def _categories(outcome: Outcome) -> list[str]:
    return [category(item) for item in (*outcome.signals, *outcome.flags)]


def _in_group(categories: list[str], prefixes: tuple[str, ...]) -> list[str]:
    return sorted(c for c in categories if c.startswith(prefixes))


def likely_cause(result: RowResult) -> str:
    """The first recorded difference that can move the score. One cause per row."""
    stored, replayed = result.stored, result.replayed
    if stored.score == 0 and not result.replayed_disqualified and replayed.score != 0:
        return "Stored as disqualified; replay passes the gates"
    if result.replayed_disqualified and stored.score != 0:
        return "Replay disqualifies; stored did not"
    stored_categories, replayed_categories = _categories(stored), _categories(replayed)
    if stored_categories.count(SALES_KEYWORD) != replayed_categories.count(SALES_KEYWORD):
        return "Sales keywords differ (skills, summary and profile text are not in the workbook)"
    for label, prefixes in SIGNAL_GROUPS:
        if _in_group(stored_categories, prefixes) != _in_group(replayed_categories, prefixes):
            return label
    return "No recorded signal explains it"


def _band(difference: int) -> str:
    if difference == 0:
        return "same"
    size = abs(difference)
    band = "1-4" if size < 5 else "5-9" if size < 10 else "10-19" if size < 20 else "20+"
    return ("+" if difference > 0 else "-") + band


def _ranked(counter: Counter[str]) -> dict[str, int]:
    return dict(sorted(counter.items(), key=lambda item: (-item[1], item[0])))


def _pooled(counter: Counter[str]) -> dict[str, int]:
    common = Counter({k: v for k, v in counter.items() if v >= MIN_CATEGORY_ROWS})
    rare = {k: v for k, v in counter.items() if v < MIN_CATEGORY_ROWS}
    pooled = _ranked(common)
    if rare:
        label = f"other ({len(rare)} kinds, each under {MIN_CATEGORY_ROWS} rows)"
        pooled[label] = sum(rare.values())
    return pooled


def summarise(results: Sequence[RowResult], profile_urls: Sequence[str]) -> dict[str, Any]:
    scored = [r for r in results if r.has_stored_score]
    differing = [r for r in scored if not r.score_match]

    missing: Counter[str] = Counter()
    added: Counter[str] = Counter()
    for result in differing:
        stored = Counter(_categories(result.stored))
        replayed = Counter(_categories(result.replayed))
        missing.update(set(stored - replayed))
        added.update(set(replayed - stored))

    bands = Counter(_band((r.replayed.score or 0) - (r.stored.score or 0)) for r in scored)
    changes: dict[str, Counter[str]] = {}
    for result in scored:
        changes.setdefault(result.stored.tier or "(none)", Counter())[result.replayed.tier] += 1

    urls = Counter(url.strip().lower().rstrip("/") for url in profile_urls if url.strip())
    return {
        "rows": len(results),
        "tracks": _ranked(Counter(r.track for r in results)),
        "stored_scores": len(scored),
        "score_matches": sum(r.score_match for r in scored),
        "tier_matches": sum(r.tier_match for r in scored),
        "score_difference": {band: bands[band] for band in DELTA_BANDS},
        "tier_changes": {tier: _ranked(counter) for tier, counter in sorted(changes.items())},
        "likely_causes": _ranked(Counter(likely_cause(r) for r in differing)),
        "categories_missing_from_replay": _pooled(missing),
        "categories_new_in_replay": _pooled(added),
        "never_scored_replay_tiers": _ranked(
            Counter(r.replayed.tier for r in results if not r.has_stored_score)
        ),
        "unreadable_numbers": _ranked(Counter(i for r in results for i in r.parse_issues)),
        "profile_urls": {
            "filled": sum(urls.values()),
            "distinct": len(urls),
            "rows_sharing_a_url": sum(count for count in urls.values() if count > 1),
        },
    }


def _pct(part: int, whole: int) -> str:
    return f"{part / whole:.1%}" if whole else "n/a"


def _table(header: tuple[str, ...], rows: list[tuple[Any, ...]]) -> list[str]:
    lines = ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
    lines += ["| " + " | ".join(str(cell) for cell in row) + " |" for row in rows]
    return [*lines, ""]


def render_report(meta: dict[str, Any], summary: dict[str, Any]) -> str:
    scored, rows = summary["stored_scores"], summary["rows"]
    differing = scored - summary["score_matches"]
    never = summary["never_scored_replay_tiers"]
    workbook = meta["workbook"]

    out = [
        f"# Baseline replay: criteria version {meta['ruleset']}",
        "",
        "Generated by `python -m replay.baseline`. Counts only. The per-row file, which holds "
        "candidate data, stays outside the repository.",
        "",
    ]
    out += _table(
        ("Run", "Value"),
        [
            ("Workbook", workbook["file"]),
            ("Workbook SHA-256", f"`{workbook['sha256']}`"),
            ("Sheet", f"{workbook['sheet']} (of {len(workbook['sheets'])})"),
            ("Ruleset", meta["ruleset"]),
            ("Run date (the ruleset's today)", meta["run_date"]),
            ("Rows read", f"{rows:,} ({meta['blank_rows_skipped']:,} blank rows skipped)"),
        ],
    )

    out += ["## Headline", ""]
    out += [
        f"- Rows with a stored score: **{scored:,}**",
        f"- Exact score match: **{summary['score_matches']:,}** "
        f"({_pct(summary['score_matches'], scored)})",
        f"- Tier match: **{summary['tier_matches']:,}** ({_pct(summary['tier_matches'], scored)})",
        f"- Rows never scored: **{rows - scored:,}**. Replayed tiers: "
        + (", ".join(f"{tier} {count:,}" for tier, count in never.items()) or "none"),
        "",
        "Each difference is listed below with its likely cause, and each needs a ruling from "
        "the criteria owner before the week 2 gate (BR-702).",
        "",
    ]

    out += ["## Score difference (replayed minus stored)", ""]
    out += _table(
        ("Difference", "Rows", "Share"),
        [(band, f"{n:,}", _pct(n, scored)) for band, n in summary["score_difference"].items()],
    )

    changes = summary["tier_changes"]
    tiers = [t for t in TIERS if t in changes or any(t in c for c in changes.values())]
    out += ["## Tiers: stored (rows) against replayed (columns)", ""]
    out += _table(
        ("Stored", *tiers),
        [(s, *(f"{changes[s].get(t, 0):,}" for t in tiers)) for s in tiers if s in changes],
    )

    out += [
        "## Likely cause of each score difference",
        "",
        "Each differing row is counted once, under the first recorded difference that can move "
        "its score.",
        "",
    ]
    out += _table(
        ("Cause", "Rows", "Share of differing rows"),
        [(cause, f"{n:,}", _pct(n, differing)) for cause, n in summary["likely_causes"].items()],
    )

    for key, title in (
        ("categories_missing_from_replay", "Stored but not replayed"),
        ("categories_new_in_replay", "Replayed but not stored"),
    ):
        out += [f"## {title} (rows with a score difference)", ""]
        out += _table(("Signal or flag", "Rows"), [(k, f"{n:,}") for k, n in summary[key].items()])

    urls = summary["profile_urls"]
    unreadable = summary["unreadable_numbers"]
    out += [
        "## Data quality",
        "",
        "- Values that could not be read as numbers: "
        + (", ".join(f"{col} {n:,}" for col, n in unreadable.items()) or "none"),
        f"- Profile URLs: {urls['filled']:,} filled, {urls['distinct']:,} distinct; "
        f"{urls['rows_sharing_a_url']:,} rows share a URL with another row",
        "",
        "## Reproduce",
        "",
        "```bash",
        'python -m replay.baseline --master "$TALENT_MASTER_PATH" '
        f"--out <directory outside the repository> --run-date {meta['run_date']}",
        "```",
        "",
        "The same workbook (hash above), ruleset and run date produce byte-identical files.",
        "",
    ]
    return "\n".join(out)
