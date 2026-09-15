"""A4: applied to scored, in seconds, and an unscored application is late, never dropped.
Made-up candidates; everything rolls back. A job left queued plays the stopped scorer."""

from datetime import timedelta

import pytest
from sqlalchemy import text

from jobs.queue import claim, run_job
from pipeline.access import Actor
from pipeline.store import create_application, create_opening
from reports.timing import LATE_AFTER_SECONDS, timing_report
from scoring.platform import JOB_KIND, handle

TA_LEAD = Actor("dev|ta-lead", sees_all=True)
FIELDS = {"age": "25", "current_title": "Sales Representative", "location": "New Cairo"}


@pytest.fixture
def conn(app_engine):
    with app_engine.connect() as connection:
        yield connection


def _application(conn, make_candidate) -> dict:
    opening = create_opening(
        conn,
        TA_LEAD,
        brand="Made-up Brand",
        department="Sales",
        track="A",
        headcount=1,
        team="team-a",
        owner_recruiter="dev|recruiter-a",
    )
    return create_application(conn, TA_LEAD, opening["id"], make_candidate(conn, **FIELDS))


def _run_scoring(conn, application_id: int) -> None:
    job_id = conn.execute(
        text("SELECT id FROM jobs.job WHERE kind = :kind AND params->>'application_id' = :a"),
        {"kind": JOB_KIND, "a": str(application_id)},
    ).scalar_one()
    job = claim(conn, job_id)
    assert job is not None
    run_job(conn, job, {JOB_KIND: handle})


def test_a_scored_application_reads_in_seconds(conn, make_candidate, use_proposed_list):
    use_proposed_list(conn)
    application = _application(conn, make_candidate)
    _run_scoring(conn, application["id"])

    report = timing_report(conn, opening_ids=[application["opening_id"]])
    (week,) = report["weeks"]
    assert (week["applications"], week["scored"], week["not_scored"]) == (1, 1, 0)
    assert 0 <= week["median_seconds"] == week["p90_seconds"] == week["slowest_seconds"] < 60
    assert report["late"] == []
    assert report["assigned"]["equals_applied"] is True


def test_a_stopped_scorer_shows_the_application_as_late(conn, make_candidate):
    application = _application(conn, make_candidate)  # its scoring job is never run
    applied = application["created_at"]
    only = {"opening_ids": [application["opening_id"]]}

    within = timing_report(conn, now=applied + timedelta(hours=1), **only)
    assert within["late"] == []
    assert within["not_scored_yet_within_the_limit"] == 1

    later = timing_report(conn, now=applied + timedelta(hours=13), **only)
    (late,) = later["late"]
    assert (late["application_id"], late["scored_at"], late["state"]) == (
        application["id"],
        None,
        "not scored",
    )
    assert late["seconds"] == pytest.approx(13 * 3600, abs=1)
    assert late["seconds"] > LATE_AFTER_SECONDS
    (week,) = later["weeks"]
    assert (week["not_scored"], week["median_seconds"]) == (1, None)


def test_the_report_holds_ids_and_times_only(conn, make_candidate):
    distinctive = {"current_title": "Zedtitle Madeup", "location": "Zedplace Madeup"}
    opening = create_opening(
        conn,
        TA_LEAD,
        brand="Made-up Brand",
        department="Sales",
        track="A",
        headcount=1,
        team="team-a",
        owner_recruiter="dev|recruiter-a",
    )
    application = create_application(
        conn, TA_LEAD, opening["id"], make_candidate(conn, **distinctive)
    )
    report = timing_report(
        conn, now=application["created_at"] + timedelta(hours=13), opening_ids=[opening["id"]]
    )
    (late,) = report["late"]
    assert set(late) == {
        "application_id",
        "opening_id",
        "applied_at",
        "scored_at",
        "seconds",
        "state",
    }
    rendered = str(report)
    for value in distinctive.values():
        assert value not in rendered
