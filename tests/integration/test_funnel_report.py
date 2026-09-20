"""A3: made-up applications walked through known moves, and the exact numbers the funnel must give
for every grouping and date range. Everything rolls back.

    Brand One (recruiter-a, team-a)                Brand Two (recruiter-b, team-b)
      A1  new > contacted > replied > phone screen   A4  new > ... > offer > hired
      A2  new > contacted > rejected (no_response)   A5  new
      A3  new > rejected (not_reachable)
    A1 has a phone and A4 an email; the others have no contact channel.
    Brand One is walked first; the clock is read; then Brand Two.
"""

import pytest
from sqlalchemy import text

from candidates.archive import archive_candidate
from pipeline.access import Actor
from pipeline.store import create_application, create_opening, move_application
from reports import ReportRefused
from reports.funnel import funnel_report

TA_LEAD = Actor("dev|ta-lead", sees_all=True)
STEPS = (
    "new",
    "contacted",
    "replied",
    "phone_screen",
    "hr_interview",
    "aptitude_test",
    "technical_interview",
    "offer",
    "hired",
)
NONE = (0, 0, 0, 0, None)
# step: (reached, moved on, rejected here, still here, conversion)
BRAND_ONE = {
    "new": (3, 2, 1, 0, 0.6667),
    "contacted": (2, 1, 1, 0, 0.5),
    "replied": (1, 1, 0, 0, 1.0),
    "phone_screen": (1, 0, 0, 1, 0.0),
    **{step: NONE for step in STEPS[4:]},
}
BRAND_TWO = {
    "new": (2, 1, 0, 1, 0.5),
    **{step: (1, 1, 0, 0, 1.0) for step in STEPS[1:8]},
    "hired": (1, 0, 0, 1, 0.0),
}
EVERYONE = {
    "new": (5, 3, 1, 1, 0.6),
    "contacted": (3, 2, 1, 0, 0.6667),
    "replied": (2, 2, 0, 0, 1.0),
    "phone_screen": (2, 1, 0, 1, 0.5),
    **{step: (1, 1, 0, 0, 1.0) for step in STEPS[4:8]},
    "hired": (1, 0, 0, 1, 0.0),
}
REASONS_ONE = {"new": {"not_reachable": 1}, "contacted": {"no_response": 1}}


@pytest.fixture
def conn(app_engine):
    with app_engine.connect() as connection:
        yield connection


@pytest.fixture
def world(conn, make_candidate, use_proposed_list):
    use_proposed_list(conn)

    def opening(brand: str, owner: str, team: str) -> dict:
        return create_opening(
            conn,
            TA_LEAD,
            brand=brand,
            department="Sales",
            track="A",
            headcount=3,
            team=team,
            owner_recruiter=owner,
        )

    def walk(opening_id: int, steps: tuple[str, ...], reject: str | None = None, **fields) -> None:
        application = create_application(conn, TA_LEAD, opening_id, make_candidate(conn, **fields))
        current = "new"
        for step in steps:
            move_application(conn, TA_LEAD, application["id"], from_step=current, to_step=step)
            current = step
        if reject:
            move_application(
                conn,
                TA_LEAD,
                application["id"],
                from_step=current,
                to_step="rejected",
                reason_code=reject,
            )

    one = opening("Brand One", "dev|recruiter-a", "team-a")
    two = opening("Brand Two", "dev|recruiter-b", "team-b")
    walk(one["id"], ("contacted", "replied", "phone_screen"), phone="made-up phone")
    walk(one["id"], ("contacted",), reject="no_response")
    walk(one["id"], (), reject="not_reachable")
    middle = conn.execute(text("SELECT clock_timestamp()")).scalar_one()
    walk(two["id"], STEPS[1:], email="made-up@example.com")
    walk(two["id"], ())
    return {"one": one, "two": two, "middle": middle, "openings": [one["id"], two["id"]]}


def _numbers(group: dict) -> dict[str, tuple]:
    return {
        s["step"]: (
            s["reached"],
            s["moved_on"],
            s["rejected_here"],
            s["still_here"],
            s["conversion"],
        )
        for s in group["steps"]
    }


def _reasons(group: dict) -> dict[str, dict]:
    return {s["step"]: s["rejected_by_reason"] for s in group["steps"] if s["rejected_by_reason"]}


def _report(conn, world, **options) -> dict[str, dict]:
    report = funnel_report(conn, opening_ids=world["openings"], **options)
    return {group["group"]: group for group in report["groups"]}


def test_every_step_from_the_list_in_force_in_order(conn, world):
    report = funnel_report(conn, opening_ids=world["openings"])
    assert report["step_list"] == "proposed-2026-09-15"
    assert tuple(step["step"] for step in report["groups"][0]["steps"]) == STEPS


def test_no_grouping(conn, world):
    (everyone,) = _report(conn, world).values()
    assert _numbers(everyone) == EVERYONE
    assert _reasons(everyone) == REASONS_ONE
    assert (everyone["applications"], everyone["candidates"]) == (5, 5)
    assert (everyone["contactable_candidates"], everyone["contactability"]) == (2, 0.4)


@pytest.mark.parametrize(
    ("group_by", "first", "second"),
    [
        ("brand", "Brand One", "Brand Two"),
        ("recruiter", "dev|recruiter-a", "dev|recruiter-b"),
        ("team", "team-a", "team-b"),
        ("opening", None, None),
    ],
)
def test_each_grouping(conn, world, group_by, first, second):
    if group_by == "opening":
        first, second = str(world["one"]["id"]), str(world["two"]["id"])
    groups = _report(conn, world, group_by=group_by)
    assert set(groups) == {first, second}
    assert _numbers(groups[first]) == BRAND_ONE
    assert _numbers(groups[second]) == BRAND_TWO
    assert _reasons(groups[first]) == REASONS_ONE
    assert _reasons(groups[second]) == {}
    assert (groups[first]["candidates"], groups[first]["contactable_candidates"]) == (3, 1)
    assert (groups[second]["candidates"], groups[second]["contactable_candidates"]) == (2, 1)


def test_a_range_ending_before_brand_two_counts_only_brand_one(conn, world):
    (everyone,) = _report(conn, world, date_to=world["middle"]).values()
    assert _numbers(everyone) == BRAND_ONE
    assert _reasons(everyone) == REASONS_ONE


def test_a_range_starting_after_brand_one_counts_only_brand_two(conn, world):
    (everyone,) = _report(conn, world, date_from=world["middle"]).values()
    assert _numbers(everyone) == BRAND_TWO


def test_an_empty_range_counts_nothing(conn, world):
    later = conn.execute(text("SELECT clock_timestamp()")).scalar_one()
    assert _report(conn, world, date_from=later) == {}


def test_grouping_by_source_is_not_offered_until_applications_carry_a_source(conn, world):
    with pytest.raises(ReportRefused, match="source"):
        funnel_report(conn, group_by="source")


@pytest.mark.parametrize("group_by", ["candidate", "stage"])
def test_an_unknown_grouping_is_refused(conn, group_by):
    with pytest.raises(ReportRefused):
        funnel_report(conn, group_by=group_by)


def test_from_must_be_before_to(conn, world):
    with pytest.raises(ReportRefused):
        funnel_report(conn, date_from=world["middle"], date_to=world["middle"])


def test_contactability_of_all_candidates_is_reported_beside_volume(conn, world):
    report = funnel_report(conn, opening_ids=world["openings"])
    everyone = report["all_candidates"]
    assert everyone["candidates"] >= 5
    assert everyone["contactable"] >= 2


def test_a_record_that_was_put_away_is_not_counted(conn, world, make_candidate):
    """Archiving is how a record that should not be there is taken out of the work (BR-205).
    A report that still counts it reports work nobody did."""
    before = _report(conn, world)["all"]
    counted = conn.execute(
        text("SELECT a.candidate_id FROM pipeline.application a WHERE a.opening_id = :o LIMIT 1"),
        {"o": world["one"]["id"]},
    ).scalar_one()

    archive_candidate(conn, int(counted), "Test data (Q-13)", "integration-test")

    after = _report(conn, world)["all"]
    assert after["applications"] == before["applications"] - 1
    assert after["candidates"] == before["candidates"] - 1


def test_a_candidate_who_asked_us_to_stop_is_not_counted(conn, world):
    """A locked record is out of every working view, and a count is a view (BR-504)."""
    before = _report(conn, world)["all"]
    counted = conn.execute(
        text("SELECT a.candidate_id FROM pipeline.application a WHERE a.opening_id = :o LIMIT 1"),
        {"o": world["two"]["id"]},
    ).scalar_one()
    conn.execute(
        text(
            "INSERT INTO core.consent_withdrawal "
            "(candidate_id, asked_how, asked_at, recorded_by) "
            "VALUES (:c, 'phone', clock_timestamp(), 'integration-test')"
        ),
        {"c": counted},
    )

    after = _report(conn, world)["all"]
    assert after["applications"] == before["applications"] - 1
