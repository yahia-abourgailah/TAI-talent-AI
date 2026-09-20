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

# Each part of the score: what it is called in the result, what it weighs, and the most it can come
# to on each track. The two tracks share the fields and use them for different things — on the
# headhunt track "sales fit" holds the title's score and "entry level" holds the tenure — so the
# labels and the full marks are read from the track, never assumed.
#
# The numbers are the criteria's own (v2026_08_04): entry 30 + 25 + 20 + 15 + 10 = 100 before
# bonuses and penalties; headhunt 15 + 25 + 20 + 25 + 15 = 100. A unit test holds them to it.
PARTS: dict[str, tuple[tuple[str, str, int], ...]] = {
    "entry": (
        ("location_score", "Where they are", 30),
        ("sales_fit_score", "How well the work fits sales", 25),
        ("entry_level_score", "How well it fits an entry-level hire", 20),
        ("education_score", "Education", 15),
        ("contact_score", "Whether we can reach them", 10),
        ("competitor_bonus", "Coming from a known brokerage", 10),
        ("platform_adjustment", "Where the profile came from", 0),
    ),
    "headhunt": (
        ("location_score", "Where they are", 15),
        ("sales_fit_score", "How senior the title is", 25),
        ("entry_level_score", "How long they have stayed", 20),
        ("competitor_bonus", "Who they work for now", 25),
        ("platform_adjustment", "Signs they are looking to move", 15),
    ),
}


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
    if mode not in PARTS:
        raise KeyError(f"no parts recorded for the {mode} track")
    candidate, _unreadable = candidate_from_fields(fields)
    result = ruleset.score_candidate(candidate, mode=mode)

    weighed = PARTS[mode]
    points = {name: int(getattr(result, name, 0) or 0) for name, _label, _most in weighed}
    parts = [
        {"part": name, "says": label, "points": points[name], "out_of": most}
        for name, label, most in weighed
    ]
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
