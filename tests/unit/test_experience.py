"""Adding up the years on a CV (BR-309, BR-703).

The dates come as the CV wrote them. Every case here is a real shape a CV uses; the point of the
table is that a total is only given when the dates can actually be read.
"""

from datetime import date

import pytest

from intake.experience import months_in, total_years

TODAY = date(2026, 9, 20)


@pytest.mark.parametrize(
    ("duration", "months"),
    [
        ("2022 - 2026", 48),
        ("2022-2026", 48),
        ("Jan 2020 - Mar 2022", 26),
        ("January 2020 – December 2021", 23),
        ("Sept. 2018 - Jun 2019", 9),
        # Only the year is on the CV, so only whole years are counted: it is never rounded up
        # on a month nobody wrote down.
        ("2019 - Present", 84),
        ("2024 - present", 24),
        ("3 years", 36),
        ("2 yrs 6 months", 30),
        ("5+ years", 60),
        ("18 months", 18),
        ("٣ سنوات", 36),
        ("2023", 12),
        ("Mar 2023", 10),
    ],
)
def test_one_job_as_a_cv_writes_it(duration, months):
    assert months_in(duration, TODAY) == months


@pytest.mark.parametrize(
    "duration",
    [
        "",
        "   ",
        None,
        "a while",
        "summer job",
        "2026 - 2022",  # backwards: not read as four years
        "1890 - 1895",  # before anybody alive worked
        "seven years",  # written out in words: not a number we will invent
    ],
)
def test_dates_we_cannot_read_are_not_guessed_at(duration):
    assert months_in(duration, TODAY) is None


def test_the_total_is_the_sum_of_the_jobs():
    assert total_years(["2022 - 2026", "Jan 2020 - Jan 2022"], TODAY) == 6


def test_a_job_nobody_can_read_is_skipped_not_counted_as_zero():
    assert total_years(["2022 - 2026", "a while"], TODAY) == 4


def test_nothing_readable_is_no_total():
    assert total_years(["a while", ""], TODAY) is None
    assert total_years([], TODAY) is None


def test_months_that_do_not_reach_a_year_are_not_a_year():
    assert total_years(["Jan 2026 - Mar 2026"], TODAY) is None


def test_overlapping_jobs_are_counted_twice_on_purpose():
    """A CV does not say which hours were which. The number is plainly a sum, and a person
    reads it; quietly dropping one of two jobs would be harder to explain."""
    assert total_years(["2020 - 2024", "2020 - 2024"], TODAY) == 8


def test_a_career_longer_than_a_lifetime_is_not_recorded():
    assert total_years(["1960 - 2026", "1960 - 2026"], TODAY) is None
