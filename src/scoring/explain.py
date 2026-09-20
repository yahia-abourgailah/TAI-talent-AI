"""Where a score came from, part by part (BR-307, CR-04).

An evaluation keeps its score, its tier and the reasons in words. It does not keep the arithmetic:
how much came from the location, how much from the education, what a bonus or a penalty moved. So
this re-runs the same criteria version over what is recorded about the candidate **now** and shows
the parts.

It never rewrites anything, and it is honest about what it is: if a field has been corrected since,
the recomputed total will differ from the stored one, and that difference is the answer — the score
on record was reached with what we knew then. The stored evaluation stays the decision; this
explains it.
"""

from dataclasses import dataclass
from typing import Any

from scoring.platform import TRACK_MODE, candidate_from_fields
from scoring.versions import RULESETS

# Each part of the score, in the order the criteria apply them.
PARTS: tuple[tuple[str, str], ...] = (
    ("location_score", "Where they are"),
    ("sales_fit_score", "How well the work fits sales"),
    ("entry_level_score", "How well it fits an entry-level hire"),
    ("education_score", "Education"),
    ("contact_score", "Whether we can reach them"),
    ("competitor_bonus", "Coming from a known brokerage"),
    ("platform_adjustment", "Where the profile came from"),
)


@dataclass(frozen=True, slots=True)
class Explanation:
    criteria_version: str
    track: str
    parts: list[dict[str, Any]]
    other_adjustments: int
    total: int
    tier: str
    disqualified: bool
    disqualify_reason: str
    signals: list[str]
    flags: list[str]

    def as_dict(self) -> dict[str, Any]:
        return {
            "criteria_version": self.criteria_version,
            "track": self.track,
            "parts": self.parts,
            "other_adjustments": self.other_adjustments,
            "total": self.total,
            "tier": self.tier,
            "disqualified": self.disqualified,
            "disqualify_reason": self.disqualify_reason or None,
            "signals": self.signals,
            "flags": self.flags,
        }


def explain(fields: dict[str, str | None], criteria_version: str, track: str) -> Explanation:
    """The same scorer, over the same fields, with its workings shown."""
    ruleset = RULESETS.get(criteria_version)
    if ruleset is None:
        raise KeyError(f"no scorer for criteria version {criteria_version}")
    mode = TRACK_MODE.get(track.upper(), track)
    candidate, _unreadable = candidate_from_fields(fields)
    result = ruleset.score_candidate(candidate, mode=mode)

    points = {name: int(getattr(result, name, 0) or 0) for name, _label in PARTS}
    parts = [{"part": name, "says": label, "points": points[name]} for name, label in PARTS]
    counted = sum(points.values())
    total = int(result.overall_score)
    return Explanation(
        criteria_version=criteria_version,
        track=mode,
        parts=parts,
        # Bonuses and penalties the criteria apply without keeping a number of their own: a private
        # university, a stale profile, how recently they were active, "open to work".
        other_adjustments=total - counted if not result.disqualified else 0,
        total=total,
        tier=str(result.priority or ""),
        disqualified=bool(result.disqualified),
        disqualify_reason=str(result.disqualify_reason or ""),
        signals=[str(signal) for signal in result.key_signals],
        flags=[str(flag) for flag in result.red_flags],
    )
