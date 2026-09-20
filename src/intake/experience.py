"""How long someone has worked, added up from the jobs on their CV (BR-309, BR-703).

The CV reader gives each job's dates as the CV wrote them — "2022 - 2026", "Jan 2020 - Mar 2022",
"3 years 6 months", "2019 - Present" — and never a total. This adds them up.

The rules, so the number can be argued with:

  * each job is read on its own, and the months are **summed**. Two jobs held at the same time
    count twice: a CV does not say which hours were which, and a total that quietly drops one is
    harder to explain than one that is plainly a sum.
  * "present", "current", "now" and the Arabic equivalents mean today.
  * a job whose dates cannot be read is skipped — not guessed at, not counted as zero-with-a-shrug.
    If no job can be read, there is no total, and the field is not recorded.
  * the total is whole years, rounded down. Eleven months of work is not a year of experience.

The result is **inferred**, never stated: it is our arithmetic, not the candidate's word, and it is
recorded as such (BR-201). On the careers page the candidate sees it in the form and can correct
it, and their correction is theirs — stated.
"""

import re
from collections.abc import Iterable
from datetime import date

MAX_YEARS = 60
# A single job longer than this is a CV we have misread, not a career.
MAX_MONTHS_PER_JOB = 50 * 12
EARLIEST_YEAR = 1950

_MONTHS = {
    "jan": 1,
    "january": 1,
    "feb": 2,
    "february": 2,
    "mar": 3,
    "march": 3,
    "apr": 4,
    "april": 4,
    "may": 5,
    "jun": 6,
    "june": 6,
    "jul": 7,
    "july": 7,
    "aug": 8,
    "august": 8,
    "sep": 9,
    "sept": 9,
    "september": 9,
    "oct": 10,
    "october": 10,
    "nov": 11,
    "november": 11,
    "dec": 12,
    "december": 12,
}
_STILL_THERE = (
    "present",
    "current",
    "now",
    "to date",
    "till date",
    "ongoing",
    "حتى الآن",
    "الان",
    "الآن",
    "حاليا",
    "حالياً",
)
_OTHER_DIGITS = "".join(chr(0x0660 + i) for i in range(10)) + "".join(
    chr(0x06F0 + i) for i in range(10)
)
_DIGITS = str.maketrans(_OTHER_DIGITS, "0123456789" * 2)
_YEARS_SAID = re.compile(r"(\d{1,2})\s*\+?\s*(?:years?|yrs?|yr|سنوات|سنة|سنه)")
_MONTHS_SAID = re.compile(r"(\d{1,2})\s*\+?\s*(?:months?|mos?\b|mths?|شهور|أشهر|اشهر|شهر)")
_DATE = re.compile(r"(?:([a-z]{3,9})[a-z.,]*\s+)?((?:19|20)\d{2})")


def _tidy(raw: str) -> str:
    return raw.translate(_DIGITS).lower().strip()


def _said_outright(text: str) -> int | None:
    """Years said outright: "3 years", "2 yrs 6 months", or the same in Arabic."""
    years = _YEARS_SAID.search(text)
    months = _MONTHS_SAID.search(text)
    if years is None and months is None:
        return None
    total = (int(years[1]) * 12 if years else 0) + (int(months[1]) if months else 0)
    return total or None


def _from_dates(text: str, today: date) -> int | None:
    """A span: "2022 - 2026", "jan 2020 - mar 2022", "2019 - present"."""
    found = [
        (_MONTHS.get(name or ""), int(year))
        for name, year in _DATE.findall(text)
        if EARLIEST_YEAR <= int(year) <= today.year + 1
    ]
    if not found:
        return None
    still_there = any(word in text for word in _STILL_THERE)
    start_month, start_year = found[0]
    if len(found) >= 2:
        end_month, end_year = found[-1]
    elif still_there:
        end_month, end_year = today.month, today.year
    else:
        # One year and nothing else: a year on a CV with no end is a year of work, not a career.
        return 12 if start_month is None else 12 - start_month + 1
    if start_month is None or end_month is None:
        # Years only. "2022 - 2026" is four years; which months they were is not on the CV.
        months = (end_year - start_year) * 12
    else:
        months = (end_year * 12 + end_month) - (start_year * 12 + start_month)
    return months if months > 0 else None


def months_in(duration: str, today: date | None = None) -> int | None:
    """One job's length in months, or None when its dates cannot be read."""
    today = today or date.today()
    text = _tidy(duration or "")
    if not text:
        return None
    months = _said_outright(text) or _from_dates(text, today)
    if months is None or not 0 < months <= MAX_MONTHS_PER_JOB:
        return None
    return months


def total_years(durations: Iterable[str], today: date | None = None) -> int | None:
    """Whole years across every job whose dates we could read. None when none could be."""
    today = today or date.today()
    months = [found for found in (months_in(one, today) for one in durations) if found]
    if not months:
        return None
    years = sum(months) // 12
    if years <= 0 or years > MAX_YEARS:
        return None
    return years
