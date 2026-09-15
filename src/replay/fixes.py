"""The fixes of 3 August 2026, as tests on a cell: which rows could the old rules have disqualified?

Before 3 August the ruleset matched some words inside other words:

  country  a non-Egypt country found inside a location word ("uk" in "Shorouk") disqualified the row
  manager  a managerial word found inside a title word ("coo" in "Coordinator") disqualified the row

Both now match whole words only (src/scoring/rulesets/v2026_08_04.py). A cell is "caught" when the
old substring matching finds a word and the whole-word matching does not: those are the rows the
fixes can have changed.
"""

import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date

from scoring.rulesets import v2026_08_04 as ruleset

FIX_DATE = date(2026, 8, 3)


def _whole_word(token: str, text: str, plural: bool = False) -> bool:
    ending = "s?" if plural else ""
    return re.search(rf"(?<!\w){re.escape(token)}{ending}(?!\w)", text) is not None


def caught_by_old_country_match(location: str) -> bool:
    text = ruleset._normalise(location)
    if not text:
        return False
    inside = any(country in text for country in ruleset.NON_EGYPT_COUNTRIES)
    whole = any(_whole_word(country, text) for country in ruleset.NON_EGYPT_COUNTRIES)
    return inside and not whole


def caught_by_old_manager_match(title: str) -> bool:
    text = ruleset._normalise(title)
    for prefix in ruleset.SENIORITY_SAFE_PREFIXES:
        text = text.replace(prefix, "").strip()
    if not text:
        return False
    inside = any(word in text for word in ruleset.SENIORITY_DISQUALIFIERS)
    whole = any(_whole_word(word, text, plural=True) for word in ruleset.SENIORITY_DISQUALIFIERS)
    return inside and not whole


@dataclass(frozen=True, slots=True)
class Fix:
    name: str
    column: str
    old_rule: str
    caught: Callable[[str], bool]


FIXES: tuple[Fix, ...] = (
    Fix(
        "country",
        "Location",
        "a non-Egypt country found inside a location word disqualified the row",
        caught_by_old_country_match,
    ),
    Fix(
        "manager",
        "Title",
        "a managerial word found inside a title word disqualified the row",
        caught_by_old_manager_match,
    ),
)
