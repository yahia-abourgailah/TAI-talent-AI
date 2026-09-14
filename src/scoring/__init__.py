"""Candidate scoring. The ruleset in force is chosen here; each ruleset module is immutable."""

from scoring.rulesets.v2026_08_04 import Candidate, ScoreResult, score_candidate

CRITERIA_VERSION = "2026-08-04"

__all__ = ["CRITERIA_VERSION", "Candidate", "ScoreResult", "score_candidate"]
