"""The review queue, reversals, roles, candidates and evaluations through the API (BR-201, BR-303,
BR-405 to BR-408). Made-up candidates; the API commits nothing, everything rolls back.
"""

from contextlib import contextmanager

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from api.app import create_app
from config import Settings
from pipeline.dev_candidate import create_demo_candidate
from pipeline.store import propose_rejection

REQUISITION = {"brand": "Made-up Brand", "department": "Sales", "track": "A", "headcount": 2}


@pytest.fixture
def api(app_engine):
    connection = app_engine.connect()

    @contextmanager
    def transaction():
        with connection.begin_nested():
            yield connection

    settings = Settings(
        _env_file=None,
        env="dev",
        auth_mode="dev",
        db_dsn="postgresql+psycopg://unused:unused@localhost:1/unused",
        redis_url="redis://localhost:1/0",
        blob_endpoint="http://localhost:1",
        blob_access_key="unused",
        blob_secret_key="unused",
    )
    client = TestClient(create_app(settings, probes={}, transaction=transaction))
    try:
        yield client, connection
    finally:
        connection.rollback()
        connection.close()


def _as(client: TestClient, account: str) -> dict[str, str]:
    token = client.post("/dev/token", json={"account": account}).json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


def _error(response) -> dict:
    return response.json()["error"]


def _setup(client, connection, account: str = "recruiter-a", team: str = "team-a"):
    headers = _as(client, account)
    requisition = client.post(
        "/v1/requisitions", json={**REQUISITION, "team": team}, headers=headers
    ).json()
    number = create_demo_candidate(connection, "integration-test", source="test-review-api")
    application = client.post(
        "/v1/applications",
        json={"requisition_id": requisition["id"], "candidate_id": f"cand_{number}"},
        headers=headers,
    )
    assert application.status_code == 201, application.text
    return application.json(), number


def _propose(connection, application: dict) -> str:
    number = int(application["id"].removeprefix("app_"))
    return f"rvw_{propose_rejection(connection, number, 'no_response', 'scoring-gate')}"


def _ids(response) -> set[str]:
    return {item["id"] for item in response.json()["items"]}


# --- Review queue ---


def test_a_person_confirms_a_negative_verdict_as_their_own_rejection(api):
    client, connection = api
    application, _ = _setup(client, connection)
    item_id = _propose(connection, application)
    headers = _as(client, "recruiter-a")

    (item,) = [
        i
        for i in client.get("/v1/review-items", headers=headers).json()["items"]
        if i["id"] == item_id
    ]
    assert (item["kind"], item["at_stage"], item["reason_code"]) == (
        "negative_verdict",
        "new",
        "no_response",
    )
    assert (item["application_id"], item["resolution"]) == (application["id"], None)

    resolved = client.post(
        f"/v1/review-items/{item_id}/resolution", json={"decision": "confirm"}, headers=headers
    )
    assert resolved.status_code == 201, resolved.text
    resolution = resolved.json()["resolution"]
    assert (resolution["decision"], resolution["resolved_by"]) == ("confirmed", "dev|recruiter-a")
    assert resolution["transition_id"].startswith("trn_")
    state = client.get(f"/v1/applications/{application['id']}", headers=headers).json()
    assert (state["current_stage"], state["outcome"]) == ("rejected", "rejected")

    again = client.post(
        f"/v1/review-items/{item_id}/resolution",
        json={"decision": "dismiss", "reason": "Changed my mind"},
        headers=headers,
    )
    assert again.status_code == 409
    assert _error(again)["code"] == "review_item_resolved"
    assert item_id not in _ids(client.get("/v1/review-items", headers=headers))
    resolved_items = client.get("/v1/review-items", params={"status": "resolved"}, headers=headers)
    assert item_id in _ids(resolved_items)


def test_dismissing_needs_a_reason_and_leaves_the_application_where_it_is(api):
    client, connection = api
    application, _ = _setup(client, connection)
    item_id = _propose(connection, application)
    headers = _as(client, "recruiter-a")
    path = f"/v1/review-items/{item_id}/resolution"

    no_reason = client.post(path, json={"decision": "dismiss"}, headers=headers)
    assert no_reason.status_code == 400
    dismissed = client.post(
        path, json={"decision": "dismiss", "reason": "Replied on another channel"}, headers=headers
    )
    assert dismissed.status_code == 201, dismissed.text
    resolution = dismissed.json()["resolution"]
    assert (resolution["decision"], resolution["reason"], resolution["transition_id"]) == (
        "dismissed",
        "Replied on another channel",
        None,
    )
    state = client.get(f"/v1/applications/{application['id']}", headers=headers).json()
    assert state["current_stage"] == "new"


def test_the_criteria_owner_reads_the_queue_but_cannot_resolve_and_scope_holds(api):
    client, connection = api
    application, _ = _setup(client, connection)
    item_id = _propose(connection, application)
    path = f"/v1/review-items/{item_id}/resolution"

    owner = _as(client, "criteria-owner")
    assert item_id in _ids(client.get("/v1/review-items", headers=owner))
    assert client.get(f"/v1/review-items/{item_id}", headers=owner).status_code == 200
    forbidden = client.post(path, json={"decision": "confirm"}, headers=owner)
    assert forbidden.status_code == 403
    assert _error(forbidden)["code"] == "forbidden"

    other = _as(client, "recruiter-b")
    assert item_id not in _ids(client.get("/v1/review-items", headers=other))
    hidden = client.post(path, json={"decision": "confirm"}, headers=other)
    assert hidden.status_code == 404


# --- Reversal ---


def test_a_rejection_is_reversed_by_a_new_application(api):
    client, connection = api
    application, _ = _setup(client, connection)
    headers = _as(client, "recruiter-a")
    rejected = client.post(
        f"/v1/applications/{application['id']}/transitions",
        json={"from_stage": "new", "to_stage": "rejected", "reason_code": "withdrew_other"},
        headers=headers,
    )
    assert rejected.status_code == 201

    reopened = client.post(
        f"/v1/applications/{application['id']}/reversal",
        json={"reason": "Rejected in error"},
        headers=headers,
    )
    assert reopened.status_code == 201, reopened.text
    body = reopened.json()
    assert body["id"] != application["id"]
    assert (body["reopens_application_id"], body["current_stage"]) == (application["id"], "new")
    original = client.get(f"/v1/applications/{application['id']}", headers=headers).json()
    assert original["current_stage"] == "rejected"

    not_rejected = client.post(
        f"/v1/applications/{body['id']}/reversal", json={"reason": "Again"}, headers=headers
    )
    assert not_rejected.status_code == 409
    assert _error(not_rejected)["code"] == "not_a_rejection"
    no_reason = client.post(
        f"/v1/applications/{application['id']}/reversal", json={"reason": " "}, headers=headers
    )
    assert no_reason.status_code == 400


# --- Candidates ---


def test_candidates_are_read_in_scope_with_where_each_field_came_from(api):
    client, connection = api
    application, number = _setup(client, connection)
    connection.execute(
        text(
            "INSERT INTO core.candidate_field "
            "(candidate_id, field, value, source, verification_status, recorded_by) VALUES "
            "(:c, 'current_title', 'Made-up Title', 'recruiter_entered', 'unverified', 'test'), "
            "(:c, 'age', NULL, 'recruiter_entered', 'not_recorded', 'test')"
        ),
        {"c": number},
    )
    headers = _as(client, "recruiter-a")
    candidate = client.get(f"/v1/candidates/cand_{number}", headers=headers)
    assert candidate.status_code == 200, candidate.text
    body = candidate.json()
    assert (body["id"], body["archived_at"]) == (f"cand_{number}", None)
    assert body["fields"]["current_title"] == {
        "value": "Made-up Title",
        "source": "recruiter_entered",
        "verification": "unverified",
        "verified_at": None,
    }
    assert body["fields"]["age"] == {"value": None, "state": "not_recorded"}

    expected = {"recruiter-b": 404, "ta-lead": 200, "admin": 200, "criteria-owner": 403}
    for account, status in expected.items():
        response = client.get(f"/v1/candidates/cand_{number}", headers=_as(client, account))
        assert response.status_code == status, account

    listed = client.get(
        "/v1/candidates", params={"requisition_id": application["requisition_id"]}, headers=headers
    ).json()["items"]
    assert [item["id"] for item in listed] == [f"cand_{number}"]
    assert set(listed[0]) == {"id", "source", "created_at", "archived"}


# --- Evaluations ---


def test_evaluations_explain_a_score_and_follow_the_candidates_scope(api):
    client, connection = api
    _application, number = _setup(client, connection)
    insert = text(
        "INSERT INTO core.evaluation "
        "(candidate_id, criteria_version_id, origin, score, tier, recommendation, signals, flags, "
        " flags_text, evaluated_at, recorded_by) "
        "VALUES (:c, '2026-08-04', :origin, :score, :tier, :recommendation, :signals, :flags, "
        " :flags_text, :evaluated_at, 'test') RETURNING id"
    )
    connection.execute(
        insert,
        {
            "c": number,
            "origin": "stored",
            "score": 0,
            "tier": "P4",
            "recommendation": "Poor Match - Archive",
            "signals": [],
            "flags": ["DISQUALIFIED: Over 32"],
            "flags_text": "DISQUALIFIED: Over 32",
            "evaluated_at": None,
        },
    )
    computed = connection.execute(
        text(
            "INSERT INTO core.evaluation "
            "(candidate_id, criteria_version_id, origin, score, tier, signals, evaluated_at, "
            " recorded_by) "
            "VALUES (:c, '2026-08-04', 'computed', 72, 'P2', ARRAY['Near New Cairo'], "
            " clock_timestamp(), 'test') RETURNING id"
        ),
        {"c": number},
    ).scalar_one()

    headers = _as(client, "recruiter-a")
    items = client.get(f"/v1/candidates/cand_{number}/evaluations", headers=headers).json()["items"]
    assert [item["origin"] for item in items] == ["computed", "stored"]
    newest, stored = items
    assert (newest["id"], newest["outcome"], newest["track"], newest["score"], newest["tier"]) == (
        f"evl_{computed}",
        "passed",
        "entry",
        72,
        "P2",
    )
    assert newest["signals"] == ["Near New Cairo"]
    assert (stored["outcome"], stored["flags"]) == ("failed_gate", ["DISQUALIFIED: Over 32"])

    path = f"/v1/evaluations/evl_{computed}"
    assert client.get(path, headers=_as(client, "criteria-owner")).status_code == 200
    assert client.get(path, headers=_as(client, "recruiter-b")).status_code == 404


# --- Admin ---


def test_an_admin_sees_and_acts_on_every_application_with_their_name(api):
    client, connection = api
    application, _ = _setup(client, connection)
    admin = _as(client, "admin")
    listed = client.get("/v1/applications", params={"limit": 200}, headers=admin)
    assert application["id"] in _ids(listed)
    moved = client.post(
        f"/v1/applications/{application['id']}/transitions",
        json={"from_stage": "new", "to_stage": "contacted"},
        headers=admin,
    )
    assert moved.status_code == 201
    assert moved.json()["actor"]["id"] == "dev|admin"
