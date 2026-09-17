"""Week 7: a borderline score opens an item only under a signed rule (BR-310), and one call lists
everything waiting for a person, each with its reason, with the oldest wait on the report
(BR-407). Made-up candidates; all rolled back.
"""

from datetime import timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from candidates import queue
from jobs.queue import claim, find_runs, run_job
from pipeline.access import Actor
from pipeline.store import create_application, create_opening
from scoring.platform import FIELD_TO_COLUMN, JOB_KIND, handle

from .conftest import sign_in

TA_LEAD = Actor("dev|ta-lead", sees_all=True)
VERSION = "2026-08-04"
# Scores 73 under criteria 2026-08-04: 2 points under the P1 line.
PROFILE = {
    "Name": "Fake Person",
    "Age": 25,
    "Title": "Sales Representative",
    "Employer": "Made-up Retail Co",
    "Location": "New Cairo",
    "Education": "bachelor",
    "Years Exp": 2,
    "Platform": "W",
}


@pytest.fixture
def conn(app_engine):
    with app_engine.connect() as connection:
        yield connection


def _sign(conn, rule: str, points: int) -> None:
    if conn.execute(
        text("SELECT 1 FROM core.criteria_borderline WHERE criteria_version_id = :v"),
        {"v": VERSION},
    ).first():
        pytest.skip("this database already holds a signed borderline rule for the version")
    conn.execute(
        text(
            "INSERT INTO core.criteria_borderline (criteria_version_id, rule, points, signed_by, "
            "signed_on, ruling, recorded_by) VALUES (:v, :rule, :points, 'Made-up Owner', "
            "'2026-09-17', 'integration test', 'integration-test')"
        ),
        {"v": VERSION, "rule": rule, "points": points},
    )


def _apply(conn, make_candidate, values=PROFILE, owner="dev|recruiter-a") -> dict:
    opening = create_opening(
        conn,
        TA_LEAD,
        brand="Made-up Brand",
        department="Sales",
        track="A",
        headcount=1,
        team="team-a",
        owner_recruiter=owner,
    )
    fields = {f: values[c] for f, c in FIELD_TO_COLUMN.items() if values.get(c) is not None}
    return create_application(conn, TA_LEAD, opening["id"], make_candidate(conn, **fields))


def _score(conn, application_id: int) -> dict:
    job_id = conn.execute(
        text(
            "SELECT id FROM jobs.job WHERE kind = :kind AND status = 'queued' "
            "AND params->>'application_id' = :a"
        ),
        {"kind": JOB_KIND, "a": str(application_id)},
    ).scalar_one()
    job = claim(conn, job_id)
    assert job is not None
    run_job(conn, job, {JOB_KIND: handle})
    (run,) = find_runs(conn, job_id=job.id, limit=1)
    assert run["outcome"] == "succeeded", run["error"]
    return run


def _borderline_items(conn, candidate_id: int) -> list:
    return conn.execute(
        text(
            "SELECT r.evaluation_id, r.tier_above, r.tier_below, r.reason_code, e.tier, e.flags "
            "FROM pipeline.review_item r JOIN core.evaluation e ON e.id = r.evaluation_id "
            "WHERE r.kind = 'borderline_score' AND r.candidate_id = :c"
        ),
        {"c": candidate_id},
    ).all()


def test_no_signed_rule_opens_no_borderline_item(conn, make_candidate, use_proposed_list):
    use_proposed_list(conn)
    application = _apply(conn, make_candidate)
    run = _score(conn, application["id"])
    assert _borderline_items(conn, application["candidate_id"]) == []
    assert run["counts"]["borderline_rule_not_signed"] == 1


def test_a_score_at_the_signed_distance_opens_an_item_with_both_tiers(
    conn, make_candidate, use_proposed_list
):
    use_proposed_list(conn)
    _sign(conn, "band", 2)
    application = _apply(conn, make_candidate)
    run = _score(conn, application["id"])

    ((_evaluation, above, below, reason, tier, flags),) = _borderline_items(
        conn, application["candidate_id"]
    )
    assert (above, below, reason) == ("P1", "P2", "near_tier_line")
    assert tier == "P2"  # the candidate keeps the tier the score gives
    assert any(f.startswith("BORDERLINE: 2 points under the P1 line (75)") for f in flags)
    assert "within 2 of a tier line" in next(f for f in flags if f.startswith("BORDERLINE"))
    assert run["counts"]["borderline_items_opened"] == 1


def test_a_score_outside_the_signed_distance_opens_nothing(conn, make_candidate, use_proposed_list):
    use_proposed_list(conn)
    _sign(conn, "band", 1)
    application = _apply(conn, make_candidate)
    _score(conn, application["id"])
    assert _borderline_items(conn, application["candidate_id"]) == []


def test_a_disqualified_candidate_is_never_borderline(conn, make_candidate, use_proposed_list):
    use_proposed_list(conn)
    _sign(conn, "band", 9)
    application = _apply(conn, make_candidate, {**PROFILE, "Age": 40})
    _score(conn, application["id"])
    assert _borderline_items(conn, application["candidate_id"]) == []


def test_a_signed_rule_is_never_changed(owner_engine):
    with owner_engine.connect() as owner:
        _sign(owner, "band", 1)
        with pytest.raises(DBAPIError), owner.begin_nested():
            owner.execute(text("UPDATE core.criteria_borderline SET points = 2"))
        with pytest.raises(DBAPIError), owner.begin_nested():
            owner.execute(
                text(
                    "INSERT INTO core.criteria_borderline (criteria_version_id, rule, points, "
                    "signed_by, signed_on, ruling, recorded_by) VALUES (:v, 'band', 2, 'x', "
                    "'2026-09-17', 'x', 'x')"
                ),
                {"v": VERSION},
            )
        owner.rollback()


def test_a_borderline_item_must_name_an_evaluation_of_its_candidate(conn, make_candidate):
    one, other = make_candidate(conn), make_candidate(conn)
    evaluation = conn.execute(
        text(
            "INSERT INTO core.evaluation (candidate_id, criteria_version_id, origin, score, tier, "
            "evaluated_at, recorded_by) VALUES (:c, :v, 'computed', 73, 'P2', now(), 'test') "
            "RETURNING id"
        ),
        {"c": one, "v": VERSION},
    ).scalar_one()
    with pytest.raises(DBAPIError), conn.begin_nested():
        conn.execute(
            text(
                "INSERT INTO pipeline.review_item (kind, candidate_id, evaluation_id, "
                "tier_above, tier_below, reason_code, proposed_by) VALUES "
                "('borderline_score', :c, :e, 'P1', 'P2', 'near_tier_line', 'test')"
            ),
            {"c": other, "e": evaluation},
        )


def test_one_call_lists_every_kind_with_its_reason_in_scope(
    api_client, make_candidate, use_proposed_list
):
    client, connection = api_client
    use_proposed_list(connection)
    _sign(connection, "band", 2)
    mine = _apply(connection, make_candidate, owner="dev|recruiter-a")
    rejected = _apply(connection, make_candidate, {**PROFILE, "Age": 40}, owner="dev|recruiter-a")
    theirs = _apply(connection, make_candidate, owner="dev|recruiter-b")
    for application in (mine, rejected, theirs):
        _score(connection, application["id"])

    recruiter = sign_in(client, "recruiter-a")
    body = client.get("/v1/review-queue", headers=recruiter).json()
    lines = {line["candidate_id"]: line for line in body["items"]}
    assert f"cand_{mine['candidate_id']}" in lines
    assert f"cand_{theirs['candidate_id']}" not in lines

    borderline = lines[f"cand_{mine['candidate_id']}"]
    assert borderline["kind"] == "borderline_score"
    assert borderline["borderline"]["tier_above"] == "P1"
    assert "between P1 and P2" in borderline["reason"]
    assert borderline["resolve_at"].startswith("/v1/candidate-review-items/rvw_")

    verdict = lines[f"cand_{rejected['candidate_id']}"]
    assert verdict["kind"] == "negative_verdict"
    assert verdict["reason"].startswith("The scoring proposes a rejection: ")
    assert "_" not in verdict["reason"]
    assert verdict["resolve_at"].startswith("/v1/review-items/rvw_")

    only = client.get(
        "/v1/review-queue", params={"kind": "negative_verdict"}, headers=recruiter
    ).json()
    assert {line["kind"] for line in only["items"]} == {"negative_verdict"}

    lead = sign_in(client, "ta-lead")
    everyone = client.get("/v1/review-queue", params={"limit": 200}, headers=lead).json()
    assert f"cand_{theirs['candidate_id']}" in {line["candidate_id"] for line in everyone["items"]}


def test_the_candidate_item_carries_its_borderline_and_its_reason(
    api_client, make_candidate, use_proposed_list
):
    client, connection = api_client
    use_proposed_list(connection)
    _sign(connection, "band", 2)
    application = _apply(connection, make_candidate)
    _score(connection, application["id"])
    body = client.get(
        "/v1/candidate-review-items",
        params={"kind": "borderline_score", "candidate_id": f"cand_{application['candidate_id']}"},
        headers=sign_in(client, "ta-lead"),
    ).json()
    (item,) = body["items"]
    assert item["borderline"]["tier_below"] == "P2"
    assert item["borderline"]["evaluation_id"].startswith("evl_")
    assert "tier line" in item["reason"]


def test_the_report_counts_open_items_and_the_oldest_wait(
    api_client, make_candidate, use_proposed_list
):
    client, connection = api_client
    use_proposed_list(connection)
    application = _apply(connection, make_candidate, {**PROFILE, "Age": 40})
    _score(connection, application["id"])

    lead = sign_in(client, "ta-lead")
    report = client.get("/v1/reports/review-queue", headers=lead).json()
    kinds = {k["kind"]: k for k in report["kinds"]}
    assert kinds["negative_verdict"]["open"] >= 1
    assert report["open"] == sum(k["open"] for k in report["kinds"])
    assert report["oldest_waiting_hours"] is not None

    funnel = client.get("/v1/reports/funnel", headers=lead).json()
    assert funnel["review_queue"]["open"] == report["open"]

    recruiter = sign_in(client, "recruiter-a")
    assert client.get("/v1/reports/review-queue", headers=recruiter).status_code == 403


def test_three_weeks_of_waiting_shows_as_a_number(conn, make_candidate, use_proposed_list):
    use_proposed_list(conn)
    application = _apply(conn, make_candidate, {**PROFILE, "Age": 40})
    _score(conn, application["id"])
    proposed_at = conn.execute(
        text("SELECT proposed_at FROM pipeline.review_item WHERE application_id = :a"),
        {"a": application["id"]},
    ).scalar_one()
    found = queue.summary(conn, TA_LEAD, now=proposed_at + timedelta(days=21))
    assert found["oldest_waiting_hours"] >= 21 * 24
    assert found["oldest_kind"] in queue.KINDS
