"""The pipeline API as the fake accounts use it (B5, B6). Made-up candidates; everything rolls back.

The app's per-request transaction is a savepoint on one test connection, which is rolled back at
the end, so the API commits nothing to the database.
"""

from contextlib import contextmanager
from itertools import pairwise

import pytest
from fastapi.testclient import TestClient

from api.app import create_app
from config import Settings
from pipeline.dev_candidate import create_demo_candidate

FORWARD = (
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
OPENING = {"brand": "Made-up Brand", "department": "Sales", "track": "A", "headcount": 3}


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


def _candidate(connection) -> int:
    return create_demo_candidate(connection, "integration-test", source="test-pipeline-api")


def _open_application(client, connection, account: str, team: str = "team-a") -> dict:
    headers = _as(client, account)
    opening = client.post("/v1/openings", json={**OPENING, "team": team}, headers=headers)
    assert opening.status_code == 201, opening.text
    application = client.post(
        "/v1/applications",
        json={"opening_id": opening.json()["id"], "candidate_id": _candidate(connection)},
        headers=headers,
    )
    assert application.status_code == 201, application.text
    return application.json()


def test_full_walk_through_every_step_as_recruiter_a(api):
    client, connection = api
    headers = _as(client, "recruiter-a")
    steps = client.get("/v1/pipeline/steps", headers=headers).json()
    assert steps["provisional"] is True

    application = _open_application(client, connection, "recruiter-a")
    assert (application["current_step"], application["owner_recruiter"]) == (
        "new",
        "dev|recruiter-a",
    )
    for from_step, to_step in pairwise(FORWARD):
        response = client.post(
            f"/v1/applications/{application['id']}/moves",
            json={"from_step": from_step, "to_step": to_step},
            headers=headers,
        )
        assert response.status_code == 201, response.text

    state = client.get(f"/v1/applications/{application['id']}", headers=headers).json()
    assert (state["current_step"], state["outcome"], state["moves"]) == ("hired", "hired", 9)
    history = client.get(f"/v1/applications/{application['id']}/moves", headers=headers).json()
    assert [move["to_step"] for move in history] == list(FORWARD)
    assert {move["moved_by"] for move in history} == {"dev|recruiter-a"}
    assert all(move["moved_at"] for move in history)

    after_hired = client.post(
        f"/v1/applications/{application['id']}/moves",
        json={"from_step": "hired", "to_step": "rejected", "reason_code": "withdrew"},
        headers=headers,
    )
    assert after_hired.status_code == 409
    assert "final" in after_hired.json()["detail"]


def test_a_move_that_is_not_allowed_is_refused(api):
    client, connection = api
    headers = _as(client, "recruiter-a")
    application = _open_application(client, connection, "recruiter-a")
    response = client.post(
        f"/v1/applications/{application['id']}/moves",
        json={"from_step": "new", "to_step": "offer"},
        headers=headers,
    )
    assert response.status_code == 409
    assert "not an allowed move" in response.json()["detail"]


def test_a_rejection_through_the_api_needs_a_listed_reason(api):
    client, connection = api
    headers = _as(client, "recruiter-a")
    application = _open_application(client, connection, "recruiter-a")
    path = f"/v1/applications/{application['id']}/moves"
    for reason in (None, "made_up_reason"):
        response = client.post(
            path,
            json={"from_step": "new", "to_step": "rejected", "reason_code": reason},
            headers=headers,
        )
        assert response.status_code == 409
    response = client.post(
        path,
        json={"from_step": "new", "to_step": "rejected", "reason_code": "withdrew"},
        headers=headers,
    )
    assert response.status_code == 201
    assert response.json()["reason_code"] == "withdrew"


def test_recruiter_a_gets_not_found_for_recruiter_b_application(api):
    client, connection = api
    theirs = _open_application(client, connection, "recruiter-b", team="team-b")
    headers = _as(client, "recruiter-a")
    missing = client.get("/v1/applications/999999999", headers=headers)

    read = client.get(f"/v1/applications/{theirs['id']}", headers=headers)
    moved = client.post(
        f"/v1/applications/{theirs['id']}/moves",
        json={"from_step": "new", "to_step": "contacted"},
        headers=headers,
    )
    history = client.get(f"/v1/applications/{theirs['id']}/moves", headers=headers)
    for response in (read, moved, history):
        assert response.status_code == 404
        assert response.json() == missing.json()

    listed = client.get("/v1/applications?limit=200", headers=headers).json()
    assert theirs["id"] not in {application["id"] for application in listed}
    unchanged = client.get(f"/v1/applications/{theirs['id']}", headers=_as(client, "recruiter-b"))
    assert unchanged.json()["current_step"] == "new"


def test_a_ta_lead_sees_every_recruiters_applications(api):
    client, connection = api
    a = _open_application(client, connection, "recruiter-a")
    b = _open_application(client, connection, "recruiter-b", team="team-b")
    headers = _as(client, "ta-lead")
    listed = {row["id"] for row in client.get("/v1/applications?limit=200", headers=headers).json()}
    assert {a["id"], b["id"]} <= listed
    assert client.get(f"/v1/applications/{b['id']}", headers=headers).status_code == 200


def test_a_recruiter_cannot_create_an_application_for_someone_else(api):
    client, connection = api
    headers = _as(client, "recruiter-a")
    opening = client.post(
        "/v1/openings", json={**OPENING, "team": "team-a"}, headers=headers
    ).json()
    response = client.post(
        "/v1/applications",
        json={
            "opening_id": opening["id"],
            "candidate_id": _candidate(connection),
            "owner_recruiter": "dev|recruiter-b",
        },
        headers=headers,
    )
    assert response.status_code == 403


def test_an_opening_is_closed_with_a_reason_through_the_api(api):
    client, _connection = api
    headers = _as(client, "recruiter-a")
    opening = client.post(
        "/v1/openings", json={**OPENING, "team": "team-a"}, headers=headers
    ).json()
    assert opening["criteria_version_id"] == "2026-08-04"
    closed = client.post(
        f"/v1/openings/{opening['id']}/close", json={"reason": "Headcount filled"}, headers=headers
    )
    assert closed.status_code == 200
    assert (closed.json()["status"], closed.json()["closed_by"]) == ("closed", "dev|recruiter-a")
    again = client.post(
        f"/v1/openings/{opening['id']}/close", json={"reason": "Again"}, headers=headers
    )
    assert again.status_code == 409


def test_errors_carry_no_candidate_data(api):
    client, connection = api
    headers = _as(client, "recruiter-a")
    application = _open_application(client, connection, "recruiter-a")
    source_key = connection.exec_driver_sql(
        f"SELECT source_key FROM core.candidate WHERE id = {int(application['candidate_id'])}"
    ).scalar_one()
    responses = [
        client.post(
            f"/v1/applications/{application['id']}/moves",
            json={"from_step": "new", "to_step": "hired"},
            headers=headers,
        ),
        client.post(
            "/v1/applications",
            json={
                "opening_id": application["opening_id"],
                "candidate_id": application["candidate_id"],
            },
            headers=headers,
        ),
        client.post(
            "/v1/applications", json={"opening_id": 1, "candidate_id": 999999999}, headers=headers
        ),
    ]
    assert [r.status_code for r in responses] == [409, 409, 404]
    for response in responses:
        assert source_key not in response.text
