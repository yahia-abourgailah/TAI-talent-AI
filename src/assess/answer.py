"""What the model is asked for, and what is accepted back (BR-305, BR-307).

The model answers in one shape and nothing else:

    {"score": 0-100,
     "reasons":  [{"says": "...", "quote": "..."}, ...],   what the CV shows, with its own words
     "missing":  ["...", ...],                             what the job asks for and the CV lacks
     "summary":  "..."}                                    one sentence

A `quote` must appear in the CV as written. A model that cannot point at the text it is talking
about is guessing, and a guess with a number on it is worse than no answer: it would be read as
evidence. So an answer whose quotes are not in the CV is refused, and the CV goes to a person with
nothing attached.

Nothing here decides anything. The score is an opinion recorded beside the candidate, and a person
reads it (CR-05).
"""

import json
import re
import unicodedata
from dataclasses import dataclass
from typing import Any

MAX_REASONS = 8
MAX_MISSING = 8
MAX_TEXT = 400
PROMPT_VERSION = "assess/2026-09-20"


class AssessmentUnreadable(Exception):
    """The answer is not in the shape we asked for. The message names the problem, never quotes."""


@dataclass(frozen=True, slots=True)
class Reason:
    says: str
    quote: str


@dataclass(frozen=True, slots=True)
class Assessment:
    score: int
    summary: str
    reasons: tuple[Reason, ...]
    missing: tuple[str, ...]
    quotes_checked: int
    quotes_found: int

    def as_signals(self) -> list[str]:
        """The reasons, as the lines an evaluation carries."""
        return [f"{reason.says} — “{reason.quote}”" for reason in self.reasons]

    def as_flags(self) -> list[str]:
        return [f"The job asks for: {item}" for item in self.missing]


def _tidy(value: Any, limit: int = MAX_TEXT) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).strip()
    return re.sub(r"\s+", " ", text)[:limit]


def _comparable(text: str) -> str:
    """Text as it reads, for finding a quote in a CV: spacing and case do not count."""
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", text)).strip().casefold()


def parse(body: str, cv_text: str) -> Assessment:
    """The answer, checked against the CV it claims to describe."""
    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        raise AssessmentUnreadable("the answer is not JSON") from None
    if not isinstance(payload, dict):
        raise AssessmentUnreadable("the answer is not a JSON object")

    raw_score = payload.get("score")
    if not isinstance(raw_score, int | float) or isinstance(raw_score, bool):
        raise AssessmentUnreadable("the answer has no score")
    score = int(max(0, min(100, round(float(raw_score)))))

    haystack = _comparable(cv_text)
    reasons: list[Reason] = []
    checked = found = 0
    for entry in (payload.get("reasons") or [])[:MAX_REASONS]:
        if not isinstance(entry, dict):
            continue
        says, quote = _tidy(entry.get("says")), _tidy(entry.get("quote"))
        if not says or not quote:
            continue
        checked += 1
        # A quote the CV does not contain is not evidence, whatever it says.
        if _comparable(quote) not in haystack:
            continue
        found += 1
        reasons.append(Reason(says=says, quote=quote))

    if checked and not found:
        raise AssessmentUnreadable(
            "not one quoted line is in the CV: the answer describes a document we did not send"
        )

    missing = tuple(
        _tidy(item) for item in (payload.get("missing") or [])[:MAX_MISSING] if _tidy(item)
    )
    return Assessment(
        score=score,
        summary=_tidy(payload.get("summary")),
        reasons=tuple(reasons),
        missing=missing,
        quotes_checked=checked,
        quotes_found=found,
    )
