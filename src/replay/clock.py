"""Pins "today" for rulesets that read the calendar.

Criteria version 2026-08-04 calls ``datetime.date.today()`` inside its recency scoring, so the same
candidate scores differently from one day to the next. A replay names its run date and holds it
fixed, or two runs over the same data would disagree (NFR-08).

Not thread-safe: it swaps ``datetime.date`` for the whole process. Keep only scoring calls inside
the block; code that checks ``isinstance(value, date)`` would see the stand-in class.
"""

import datetime as _dt
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Self


@contextmanager
def pinned_today(run_date: _dt.date) -> Iterator[None]:
    real_date = _dt.date

    class PinnedDate(_dt.date):
        @classmethod
        def today(cls) -> Self:
            return cls(run_date.year, run_date.month, run_date.day)

    setattr(_dt, "date", PinnedDate)  # noqa: B010 - assigning to a module's class attribute
    try:
        yield
    finally:
        setattr(_dt, "date", real_date)  # noqa: B010
