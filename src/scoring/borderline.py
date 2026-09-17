"""Borderline: a score close enough to a tier line that a person looks at it (BR-310).

A borderline candidate keeps the tier the score gives: nothing is rounded up or down. The item a
person sees names the tier above the line and the tier below it.

How close is close is the criteria owner's decision, recorded per criteria version in
core.criteria_borderline (migration 0014), never in code. A version with no signed rule opens no
borderline items. The rule is one of:

    band        the score is within `points` of a line, either side (the line itself included)
    below_line  the score is 1 to `points` under a line: the candidate who just missed the tier

The lines are the ruleset's own cut-offs. LINES must match the ruleset; a unit test holds them
together, and a new criteria version with different cut-offs adds its own entry.
"""

from dataclasses import dataclass
from typing import Literal

Rule = Literal["band", "below_line"]
RULES: tuple[Rule, ...] = ("band", "below_line")
MAX_POINTS = 9  # lines are 20 apart, so a candidate is never near two of them

# criteria version -> track mode -> (line, tier at or above it, tier below it), highest first.
LINES: dict[str, dict[str, tuple[tuple[int, str, str], ...]]] = {
    "2026-08-04": {
        "entry": ((75, "P1", "P2"), (55, "P2", "P3"), (35, "P3", "P4")),
        "headhunt": ((80, "T1", "T2"), (60, "T2", "T3"), (40, "T3", "T4")),
    },
}


@dataclass(frozen=True, slots=True)
class Policy:
    rule: Rule
    points: int

    def __post_init__(self) -> None:
        if self.rule not in RULES:
            raise ValueError(f"unknown borderline rule {self.rule!r}")
        if not 1 <= self.points <= MAX_POINTS:
            raise ValueError(f"borderline points must be 1 to {MAX_POINTS}")

    def describe(self) -> str:
        if self.rule == "band":
            return f"within {self.points} of a tier line"
        return f"1 to {self.points} under a tier line"


@dataclass(frozen=True, slots=True)
class Borderline:
    line: int
    tier_above: str
    tier_below: str
    distance: int  # score minus line: negative is under the line

    def explain(self, policy: Policy, criteria_version: str) -> str:
        """The sentence stored with the evaluation. No candidate value is in it."""
        if self.distance == 0:
            where = f"exactly on the {self.tier_above} line ({self.line})"
        else:
            side = "under" if self.distance < 0 else "over"
            points = abs(self.distance)
            unit = "point" if points == 1 else "points"
            where = f"{points} {unit} {side} the {self.tier_above} line ({self.line})"
        return (
            f"BORDERLINE: {where}; between {self.tier_above} and {self.tier_below}. "
            f"Rule: {policy.describe()}, criteria {criteria_version}"
        )


def lines_for(criteria_version: str, mode: str) -> tuple[tuple[int, str, str], ...]:
    try:
        return LINES[criteria_version][mode]
    except KeyError:
        raise KeyError(f"no tier lines recorded for criteria {criteria_version} {mode}") from None


def near_line(score: int, criteria_version: str, mode: str, policy: Policy) -> Borderline | None:
    """The line this score is borderline to under the policy, or None."""
    for line, above, below in lines_for(criteria_version, mode):
        distance = score - line
        if policy.rule == "band":
            hit = abs(distance) <= policy.points
        else:
            hit = -policy.points <= distance < 0
        if hit:
            return Borderline(line, above, below, distance)
    return None
