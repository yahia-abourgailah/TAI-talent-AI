"""A1 without a database: the platform builds the replay's input, and a disqualification maps to
the agreed reason or to nothing. Made-up profiles only."""

import pytest

from replay.mapping import map_row
from replay.workbook import MasterRow
from scoring.platform import FIELD_TO_COLUMN, candidate_from_fields, reason_for
from scoring.rulesets.v2026_08_04 import score_candidate

WORKBOOK = {
    "Name": "Fake Person",
    "Age": 25,
    "Title": "Sales Representative",
    "Employer": "Made-up Retail Co",
    "Location": "New Cairo",
    "Education": "bachelor",
    "Years Exp": 2,
    "Platform": "W",
    "Profile URL": "https://example.com/in/fake-person",
}


def _fields(values: dict[str, object]) -> dict[str, str | None]:
    """The same values as the platform stores them: text, one row per field."""
    return {
        field: None if values.get(column) is None else str(values[column])
        for field, column in FIELD_TO_COLUMN.items()
    }


def test_the_platform_builds_the_same_candidate_as_the_replay():
    candidate, unreadable = candidate_from_fields(_fields(WORKBOOK))
    assert candidate == map_row(MasterRow(2, WORKBOOK)).candidate
    assert unreadable == ()


def test_last_active_is_not_given_to_the_scorer():
    candidate, _ = candidate_from_fields({**_fields(WORKBOOK), "source_last_active": "2026-09-01"})
    assert candidate.last_active == ""


def test_an_unreadable_number_is_reported_not_guessed():
    candidate, unreadable = candidate_from_fields({**_fields(WORKBOOK), "age": "twenty"})
    assert candidate.age is None
    assert unreadable == ("Age",)


@pytest.mark.parametrize(
    ("changes", "prefix", "reason"),
    [
        ({"Location": "Alexandria"}, "Outside Cairo", "outside_hiring_area"),
        ({"Age": 40}, "Over 32", "age_outside_range"),
        ({"Age": 19}, "Under 21", "age_outside_range"),
        ({"Title": "Sales Manager"}, "Managerial/director title", "experience_not_a_fit"),
        ({"Years Exp": 12}, "11+ years experience", "experience_not_a_fit"),
        ({"Title": "Team Leader"}, "Team Leader / Supervisor", None),
    ],
)
def test_a_scorer_disqualification_maps_to_the_agreed_reason(changes, prefix, reason):
    candidate, _ = candidate_from_fields(_fields({**WORKBOOK, **changes}))
    result = score_candidate(candidate, mode="entry")
    assert result.disqualified
    assert result.disqualify_reason.startswith(prefix)
    assert reason_for(result.disqualify_reason) == (True, reason)


def test_a_disqualification_with_no_agreed_reason_is_not_guessed():
    assert reason_for("Employer not recognised (not RE, brokerage, or crossover industry)") == (
        False,
        None,
    )
    assert reason_for("No tenured title match") == (False, None)


def test_the_full_mark_of_each_part_is_the_criteria_own(monkeypatch):
    """The explanation says what each part is out of. Those numbers are the ruleset's, and this
    holds them to it: a candidate who is the best possible on one part scores exactly its full
    mark, so the column cannot quietly drift from the criteria it describes."""
    from scoring.explain import PARTS, explain

    best_entry = {
        "full_name": "Best Possible Entry",
        "age": "24",
        "location": "New Cairo",
        "current_title": "Sales Representative",
        "current_employer": "Coldwell Banker",
        "education": "bachelor",
        "years_experience": "1",
        "phone": "01000000001",
        "email": "best@example.com",
    }
    found = explain(best_entry, "2026-08-04", "A")
    out_of = {part["part"]: part["out_of"] for part in found.parts}
    points = {part["part"]: part["points"] for part in found.parts}
    assert out_of["location_score"] == 30 and points["location_score"] == 30
    assert out_of["education_score"] == 15 and points["education_score"] == 15
    assert out_of["contact_score"] == 10 and points["contact_score"] == 10
    # The five parts of the entry track are a hundred between them, before bonuses and penalties.
    core = (
        "location_score",
        "sales_fit_score",
        "entry_level_score",
        "education_score",
        "contact_score",
    )
    assert sum(out_of[name] for name in core) == 100
    # No part is ever scored above its full mark.
    for part in found.parts:
        assert part["points"] <= part["out_of"] or part["out_of"] == 0

    headhunt = sum(most for _name, _says, most in PARTS["headhunt"])
    assert headhunt == 100
