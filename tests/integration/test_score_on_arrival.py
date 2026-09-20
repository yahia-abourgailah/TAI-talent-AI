"""A1: a new application is scored straight away, exactly as the replay would score the same
fields, and a disqualification only ever opens a review item. Made-up candidates; all rolled back.
"""

import uuid

import pytest
from sqlalchemy import text

from candidates import reads
from jobs.queue import claim, find_runs, run_job
from pipeline.access import Actor
from pipeline.store import create_application, create_opening, get_application, move_history
from replay.mapping import map_row
from replay.workbook import MasterRow
from scoring.platform import FIELD_TO_COLUMN, JOB_KIND, handle
from scoring.rulesets.v2026_08_04 import score_candidate

from .conftest import sign_in

TA_LEAD = Actor("dev|ta-lead", sees_all=True)
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


def _fields(values: dict[str, object]) -> dict[str, object]:
    return {f: values[c] for f, c in FIELD_TO_COLUMN.items() if values.get(c) is not None}


def _apply(conn, make_candidate, values: dict[str, object], track: str = "A") -> dict:
    opening = create_opening(
        conn,
        TA_LEAD,
        brand="Made-up Brand",
        department="Sales",
        track=track,
        headcount=2,
        team="team-a",
        owner_recruiter="dev|recruiter-a",
    )
    return create_application(conn, TA_LEAD, opening["id"], make_candidate(conn, **_fields(values)))


def _queued_job(conn, application_id: int) -> int:
    return int(
        conn.execute(
            text(
                "SELECT id FROM jobs.job WHERE kind = :kind AND status = 'queued' "
                "AND params->>'application_id' = :application"
            ),
            {"kind": JOB_KIND, "application": str(application_id)},
        ).scalar_one()
    )


def _score(conn, application_id: int) -> dict:
    job = claim(conn, _queued_job(conn, application_id))
    assert job is not None
    run_job(conn, job, {JOB_KIND: handle})
    (run,) = find_runs(conn, job_id=job.id, limit=1)
    assert run["outcome"] == "succeeded", run["error"]
    return run


def _evaluations(conn, candidate_id: int):
    return conn.execute(
        text(
            "SELECT id, origin, score, tier, criteria_version_id, evaluated_at "
            "FROM core.evaluation WHERE candidate_id = :c ORDER BY id"
        ),
        {"c": candidate_id},
    ).all()


def _review_items(conn, application_id: int) -> list[tuple]:
    rows = conn.execute(
        text("SELECT reason_code, proposed_by FROM pipeline.review_item WHERE application_id = :a"),
        {"a": application_id},
    )
    return [tuple(row) for row in rows]


def test_creating_an_application_queues_its_scoring(conn, make_candidate):
    application = _apply(conn, make_candidate, PROFILE)
    assert _queued_job(conn, application["id"])


def test_the_computed_score_equals_the_replay_for_the_same_fields(
    conn, make_candidate, use_proposed_list
):
    use_proposed_list(conn)
    application = _apply(conn, make_candidate, PROFILE)
    _score(conn, application["id"])

    (evaluation,) = _evaluations(conn, application["candidate_id"])
    expected = score_candidate(map_row(MasterRow(2, PROFILE)).candidate, mode="entry")
    assert (evaluation.origin, evaluation.criteria_version_id) == ("computed", "2026-08-04")
    assert (evaluation.score, evaluation.tier) == (expected.overall_score, expected.priority)
    assert evaluation.evaluated_at >= application["created_at"]


def test_a_track_b_opening_scores_in_track_b(conn, make_candidate, use_proposed_list):
    use_proposed_list(conn)
    application = _apply(conn, make_candidate, PROFILE, track="B")
    _score(conn, application["id"])
    (evaluation,) = _evaluations(conn, application["candidate_id"])
    expected = score_candidate(map_row(MasterRow(2, PROFILE)).candidate, mode="headhunt")
    assert (evaluation.score, evaluation.tier) == (expected.overall_score, expected.priority)


def test_a_re_score_is_a_new_evaluation_never_an_overwrite(conn, make_candidate, use_proposed_list):
    use_proposed_list(conn)
    application = _apply(conn, make_candidate, PROFILE)
    _score(conn, application["id"])
    conn.execute(
        text(
            "INSERT INTO jobs.job (kind, params, requested_by) "
            "VALUES (:kind, jsonb_build_object('application_id', CAST(:a AS bigint)), 'test')"
        ),
        {"kind": JOB_KIND, "a": application["id"]},
    )
    _score(conn, application["id"])
    first, second = _evaluations(conn, application["candidate_id"])
    assert first.id != second.id
    assert first.score == second.score
    assert second.evaluated_at >= first.evaluated_at


@pytest.mark.parametrize(
    ("changes", "reason"),
    [
        ({"Location": "Alexandria"}, "outside_hiring_area"),
        ({"Age": 40}, "age_outside_range"),
        ({"Title": "Sales Manager"}, "experience_not_a_fit"),
    ],
)
def test_a_disqualification_opens_a_review_item_and_never_rejects(
    conn, make_candidate, use_proposed_list, changes, reason
):
    use_proposed_list(conn)
    application = _apply(conn, make_candidate, {**PROFILE, **changes})
    run = _score(conn, application["id"])

    assert _review_items(conn, application["id"]) == [(reason, "scoring-worker")]
    assert run["counts"]["review_items_opened"] == 1
    assert get_application(conn, TA_LEAD, application["id"])["current_step"] == "new"
    assert [m["to_step"] for m in move_history(conn, TA_LEAD, application["id"])] == ["new"]
    (evaluation,) = _evaluations(conn, application["candidate_id"])
    assert evaluation.score == 0


def test_routing_to_track_b_is_not_a_rejection(conn, make_candidate, use_proposed_list):
    use_proposed_list(conn)
    application = _apply(conn, make_candidate, {**PROFILE, "Title": "Team Leader"})
    run = _score(conn, application["id"])
    assert _review_items(conn, application["id"]) == []
    assert run["counts"]["routed_to_other_track"] == 1


def test_scoring_again_does_not_open_a_second_review_item(conn, make_candidate, use_proposed_list):
    use_proposed_list(conn)
    application = _apply(conn, make_candidate, {**PROFILE, "Age": 40})
    _score(conn, application["id"])
    conn.execute(
        text(
            "INSERT INTO jobs.job (kind, params, requested_by) "
            "VALUES (:kind, jsonb_build_object('application_id', CAST(:a AS bigint)), 'test')"
        ),
        {"kind": JOB_KIND, "a": application["id"]},
    )
    run = _score(conn, application["id"])
    assert len(_review_items(conn, application["id"])) == 1
    assert run["counts"]["review_item_already_open"] == 1


def test_a_reason_missing_from_the_list_in_force_is_listed_not_guessed(
    conn, make_candidate, use_provisional_list
):
    use_provisional_list(conn)  # the BRD placeholder list has no outside_hiring_area
    application = _apply(conn, make_candidate, {**PROFILE, "Location": "Alexandria"})
    run = _score(conn, application["id"])
    assert _review_items(conn, application["id"]) == []
    assert [item["code"] for item in run["unresolved"]] == ["reason_not_on_the_list_in_force"]


def test_an_application_created_through_the_api_is_scored_and_never_rejected(
    api_client, make_candidate, use_proposed_list
):
    client, connection = api_client
    use_proposed_list(connection)
    headers = sign_in(client, "recruiter-a")
    requisition = client.post(
        "/v1/requisitions",
        json={
            "brand": "Made-up Brand",
            "department": "Sales",
            "track": "A",
            "headcount": 2,
            "team": "team-a",
        },
        headers=headers,
    ).json()

    for values, expected_review in ((PROFILE, []), ({**PROFILE, "Age": 40}, ["age_outside_range"])):
        candidate = make_candidate(connection, **_fields(values))
        application = client.post(
            "/v1/applications",
            json={"requisition_id": requisition["id"], "candidate_id": f"cand_{candidate}"},
            headers=headers,
        ).json()
        number = int(application["id"].removeprefix("app_"))
        _score(connection, number)

        (evaluation,) = _evaluations(connection, candidate)
        replayed = score_candidate(map_row(MasterRow(2, values)).candidate, mode="entry")
        assert evaluation.score == replayed.overall_score
        assert [r for r, _by in _review_items(connection, number)] == expected_review
        state = client.get(f"/v1/applications/{application['id']}", headers=headers).json()
        assert state["current_stage"] == "new"


@pytest.mark.parametrize(("changes", "outcome"), [({}, "passed"), ({"Age": 40}, "failed_gate")])
def test_a_score_on_arrival_is_an_event_and_reads_with_its_outcome(
    conn, make_candidate, use_proposed_list, changes, outcome
):
    """Scoring on arrival (migration 0008) meets the CRM events (0007) and the evaluation reads:
    the candidate.scored event and GET /v1/evaluations agree on passed or failed_gate."""
    use_proposed_list(conn)
    application = _apply(conn, make_candidate, {**PROFILE, **changes})
    _score(conn, application["id"])
    (evaluation,) = _evaluations(conn, application["candidate_id"])

    scored = conn.execute(
        text(
            "SELECT data FROM integration.event "
            "WHERE type = 'candidate.scored' AND data->>'evaluation_id' = :e"
        ),
        {"e": f"evl_{evaluation.id}"},
    ).scalar_one()
    assert (scored["outcome"], scored["tier"]) == (outcome, evaluation.tier)
    assert reads.outcome(reads.get_evaluation(conn, TA_LEAD, evaluation.id)) == outcome

    kinds = (
        conn.execute(
            text(
                "SELECT data->>'kind' FROM integration.event "
                "WHERE type = 'review.item_created' AND application_id = :a"
            ),
            {"a": application["id"]},
        )
        .scalars()
        .all()
    )
    assert kinds == ([] if outcome == "passed" else ["negative_verdict"])


def test_an_evaluation_can_show_the_sums_behind_its_score(api_client, make_candidate):
    """BR-307: an old decision is explained from what was saved, not from anyone's memory.

    The evaluation keeps the score, the tier and the reasons in words. The arithmetic is not kept,
    so it is worked out again from what is recorded — and the answer says whether that still comes
    to the same total.
    """
    client, connection = api_client
    application = _apply(connection, make_candidate, PROFILE)
    _score(connection, application["id"])

    candidate = f"cand_{application['candidate_id']}"
    evaluation = client.get(
        f"/v1/candidates/{candidate}/evaluations", headers=sign_in(client, "ta-lead")
    ).json()["items"][0]

    explained = client.get(
        f"/v1/evaluations/{evaluation['id']}/explanation", headers=sign_in(client, "ta-lead")
    )
    assert explained.status_code == 200, explained.text
    found = explained.json()

    assert found["matches_stored"] is True
    assert found["total"] == int(evaluation["score"])
    assert found["tier"] == evaluation["tier"]
    # The parts add up to the total, with whatever the criteria applied without a number of its own.
    assert (
        sum(part["points"] for part in found["parts"]) + found["other_adjustments"]
        == found["total"]
    )
    assert {part["part"] for part in found["parts"]} >= {"location_score", "education_score"}
    assert found["signals"], "a score says what earned it"


def test_the_explanation_says_when_the_record_has_changed_since(api_client, make_candidate):
    """A corrected field gives a different total today. The stored score stays the decision."""
    client, connection = api_client
    headers = sign_in(client, "ta-lead")
    application = _apply(connection, make_candidate, PROFILE)
    _score(connection, application["id"])
    candidate = f"cand_{application['candidate_id']}"
    evaluation = client.get(f"/v1/candidates/{candidate}/evaluations", headers=headers).json()[
        "items"
    ][0]

    # Somebody checks the location and finds it wrong: not Cairo at all.
    corrected = client.post(
        f"/v1/candidates/{candidate}/fields/location/verification",
        json={"value": "Alexandria"},
        headers={**headers, "Idempotency-Key": str(uuid.uuid4())},
    )
    assert corrected.status_code == 201, corrected.text

    found = client.get(f"/v1/evaluations/{evaluation['id']}/explanation", headers=headers).json()
    assert found["matches_stored"] is False
    assert found["stored_score"] == evaluation["score"]
    assert found["total"] != evaluation["score"]
