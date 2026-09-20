"""The frozen pipeline API as the fake accounts use it (BR-401 to BR-410, API plan sections 5, 6).

The app's per-request transaction is a savepoint on one test connection, which is rolled back at
the end, so the API commits nothing to the database. Made-up candidates only.
"""

import uuid
from contextlib import contextmanager
from itertools import pairwise

import pytest
from fastapi.testclient import TestClient

from api.app import create_app
from candidates.archive import archive_candidate
from config import Settings
from pipeline.dev_candidate import create_demo_candidate

from .conftest import sign_in

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
REQUISITION = {"brand": "Made-up Brand", "department": "Sales", "track": "A", "headcount": 3}


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


def _candidate(connection) -> str:
    number = create_demo_candidate(connection, "integration-test", source="test-pipeline-api")
    return f"cand_{number}"


def _requisition(client, headers, **overrides) -> dict:
    body = {**REQUISITION, "team": "team-a", **overrides}
    response = client.post("/v1/requisitions", json=body, headers=headers)
    assert response.status_code == 201, response.text
    return response.json()


def _open_application(client, connection, account: str, team: str = "team-a") -> dict:
    headers = _as(client, account)
    requisition = _requisition(client, headers, team=team)
    application = client.post(
        "/v1/applications",
        json={"requisition_id": requisition["id"], "candidate_id": _candidate(connection)},
        headers=headers,
    )
    assert application.status_code == 201, application.text
    return application.json()


def _move(client, headers, application, from_stage, to_stage, reason_code=None, key=None):
    extra = {"Idempotency-Key": key} if key else {}
    return client.post(
        f"/v1/applications/{application['id']}/transitions",
        json={"from_stage": from_stage, "to_stage": to_stage, "reason_code": reason_code},
        headers={**headers, **extra},
    )


def _error(response) -> dict:
    return response.json()["error"]


def test_full_walk_through_every_stage_as_recruiter_a(api):
    client, connection = api
    headers = _as(client, "recruiter-a")
    stages = client.get("/v1/reference/stages", headers=headers).json()
    assert stages["provisional"] is True
    assert [stage["code"] for stage in stages["stages"]][:2] == ["new", "contacted"]

    application = _open_application(client, connection, "recruiter-a")
    assert application["id"].startswith("app_")
    assert application["candidate_id"].startswith("cand_")
    assert application["requisition_id"].startswith("req_")
    assert (application["current_stage"], application["owner_id"]) == ("new", "dev|recruiter-a")
    assert application["allowed_transitions"] == ["contacted", "rejected"]
    for from_stage, to_stage in pairwise(FORWARD):
        response = _move(client, headers, application, from_stage, to_stage)
        assert response.status_code == 201, response.text
        assert response.json()["id"].startswith("trn_")

    state = client.get(f"/v1/applications/{application['id']}", headers=headers).json()
    assert (state["current_stage"], state["outcome"], state["transitions"]) == ("hired", "hired", 9)
    assert state["allowed_transitions"] == []
    history = client.get(f"/v1/applications/{application['id']}/transitions", headers=headers)
    items = history.json()["items"]
    assert [item["to_stage"] for item in items] == list(FORWARD)
    assert {item["actor"]["id"] for item in items} == {"dev|recruiter-a"}
    assert {item["actor"]["kind"] for item in items} == {"person"}
    assert all(item["occurred_at"].endswith("Z") for item in items)

    after_hired = _move(client, headers, application, "hired", "rejected", "withdrew_other")
    assert after_hired.status_code == 409
    assert _error(after_hired)["code"] == "stage_is_final"


def test_a_transition_that_is_not_allowed_lists_the_allowed_ones(api):
    client, connection = api
    headers = _as(client, "recruiter-a")
    application = _open_application(client, connection, "recruiter-a")
    response = _move(client, headers, application, "new", "offer")
    assert response.status_code == 409
    error = _error(response)
    assert error["code"] == "transition_not_allowed"
    assert error["details"] == {
        "from_stage": "new",
        "to_stage": "offer",
        "allowed": ["contacted", "rejected"],
    }


def test_a_stale_stage_is_refused_with_the_stage_it_is_at_now(api):
    client, connection = api
    headers = _as(client, "recruiter-a")
    application = _open_application(client, connection, "recruiter-a")
    assert _move(client, headers, application, "new", "contacted").status_code == 201
    stale = _move(client, headers, application, "new", "contacted")
    assert stale.status_code == 409
    assert _error(stale)["code"] == "stage_changed"
    assert _error(stale)["details"]["current_stage"] == "contacted"


def test_a_rejection_through_the_api_needs_a_listed_reason(api):
    client, connection = api
    headers = _as(client, "recruiter-a")
    application = _open_application(client, connection, "recruiter-a")
    for reason in (None, "made_up_reason"):
        response = _move(client, headers, application, "new", "rejected", reason)
        assert response.status_code == 409
        assert _error(response)["code"] == "rejection_reason_required"
    response = _move(client, headers, application, "new", "rejected", "withdrew_other")
    assert response.status_code == 201
    assert response.json()["reason_code"] == "withdrew_other"


def test_recruiter_a_gets_not_found_for_recruiter_b_application(api):
    client, connection = api
    theirs = _open_application(client, connection, "recruiter-b", team="team-b")
    headers = _as(client, "recruiter-a")
    missing = client.get("/v1/applications/app_999999999", headers=headers)
    malformed = client.get("/v1/applications/app_nope", headers=headers)

    read = client.get(f"/v1/applications/{theirs['id']}", headers=headers)
    moved = _move(client, headers, theirs, "new", "contacted")
    history = client.get(f"/v1/applications/{theirs['id']}/transitions", headers=headers)
    for response in (malformed, read, moved, history):
        assert response.status_code == 404
        assert _error(response)["code"] == _error(missing)["code"] == "not_found"
        assert _error(response)["message"] == _error(missing)["message"]

    listed = client.get("/v1/applications", params={"limit": 200}, headers=headers).json()
    assert theirs["id"] not in {application["id"] for application in listed["items"]}
    unchanged = client.get(f"/v1/applications/{theirs['id']}", headers=_as(client, "recruiter-b"))
    assert unchanged.json()["current_stage"] == "new"


def test_a_ta_lead_sees_every_recruiters_applications(api):
    client, connection = api
    a = _open_application(client, connection, "recruiter-a")
    b = _open_application(client, connection, "recruiter-b", team="team-b")
    headers = _as(client, "ta-lead")
    listed = client.get("/v1/applications", params={"limit": 200}, headers=headers).json()
    assert {a["id"], b["id"]} <= {row["id"] for row in listed["items"]}
    assert client.get(f"/v1/applications/{b['id']}", headers=headers).status_code == 200


def test_a_recruiter_cannot_create_an_application_for_someone_else(api):
    client, connection = api
    headers = _as(client, "recruiter-a")
    requisition = _requisition(client, headers)
    response = client.post(
        "/v1/applications",
        json={
            "requisition_id": requisition["id"],
            "candidate_id": _candidate(connection),
            "owner_id": "dev|recruiter-b",
        },
        headers=headers,
    )
    assert response.status_code == 403
    assert _error(response)["code"] == "forbidden"


def test_a_requisition_is_closed_with_a_reason_and_then_takes_nothing(api):
    client, connection = api
    headers = _as(client, "recruiter-a")
    requisition = _requisition(client, headers)
    assert requisition["criteria_version"] == "2026-08-04"
    closed = client.post(
        f"/v1/requisitions/{requisition['id']}/close",
        json={"reason": "Headcount filled"},
        headers=headers,
    )
    assert closed.status_code == 200
    assert (closed.json()["status"], closed.json()["closed_by"]) == ("closed", "dev|recruiter-a")

    again = client.post(
        f"/v1/requisitions/{requisition['id']}/close", json={"reason": "Again"}, headers=headers
    )
    late = client.post(
        "/v1/applications",
        json={"requisition_id": requisition["id"], "candidate_id": _candidate(connection)},
        headers=headers,
    )
    for response in (again, late):
        assert response.status_code == 409
        assert _error(response)["code"] == "requisition_closed"


def test_errors_carry_no_candidate_data(api):
    client, connection = api
    headers = _as(client, "recruiter-a")
    application = _open_application(client, connection, "recruiter-a")
    candidate_number = int(application["candidate_id"].removeprefix("cand_"))
    source_key = connection.exec_driver_sql(
        f"SELECT source_key FROM core.candidate WHERE id = {candidate_number}"
    ).scalar_one()
    responses = [
        _move(client, headers, application, "new", "hired"),
        client.post(
            "/v1/applications",
            json={
                "requisition_id": application["requisition_id"],
                "candidate_id": application["candidate_id"],
            },
            headers=headers,
        ),
        client.post(
            "/v1/applications",
            json={"requisition_id": "req_999999999", "candidate_id": "cand_999999999"},
            headers=headers,
        ),
    ]
    assert [r.status_code for r in responses] == [409, 409, 404]
    assert _error(responses[1])["code"] == "already_exists"
    for response in responses:
        assert source_key not in response.text


def test_the_same_idempotency_key_returns_the_first_response(api):
    client, _connection = api
    key = str(uuid.uuid4())
    brand = f"Made-up Brand {uuid.uuid4().hex[:8]}"
    body = {**REQUISITION, "brand": brand, "team": "team-a"}
    headers = {**_as(client, "recruiter-a"), "Idempotency-Key": key}

    first = client.post("/v1/requisitions", json=body, headers=headers)
    again = client.post("/v1/requisitions", json=body, headers=headers)
    assert (first.status_code, again.status_code) == (201, 201)
    assert again.json() == first.json()
    assert again.headers["idempotent-replayed"] == "true"
    listed = client.get("/v1/requisitions", params={"brand": brand}, headers=headers).json()
    assert [row["id"] for row in listed["items"]] == [first.json()["id"]]

    different = client.post("/v1/requisitions", json={**body, "headcount": 9}, headers=headers)
    assert different.status_code == 409
    assert _error(different)["code"] == "idempotency_key_reused"

    someone_else = {**_as(client, "recruiter-b"), "Idempotency-Key": key}
    theirs = client.post("/v1/requisitions", json={**body, "team": "team-b"}, headers=someone_else)
    assert theirs.status_code == 201
    assert theirs.json()["id"] != first.json()["id"]


def test_a_retried_transition_moves_the_application_once(api):
    client, connection = api
    headers = _as(client, "recruiter-a")
    application = _open_application(client, connection, "recruiter-a")
    key = str(uuid.uuid4())
    first = _move(client, headers, application, "new", "contacted", key=key)
    again = _move(client, headers, application, "new", "contacted", key=key)
    assert (first.status_code, again.status_code) == (201, 201)
    assert again.json()["id"] == first.json()["id"]
    history = client.get(f"/v1/applications/{application['id']}/transitions", headers=headers)
    assert [item["to_stage"] for item in history.json()["items"]] == ["new", "contacted"]


def test_lists_come_in_cursor_pages_newest_first(api):
    client, _connection = api
    headers = _as(client, "recruiter-a")
    brand = f"Paged Brand {uuid.uuid4().hex[:8]}"
    made = [_requisition(client, headers, brand=brand)["id"] for _ in range(3)]

    first = client.get("/v1/requisitions", params={"brand": brand, "limit": 2}, headers=headers)
    page = first.json()
    assert [row["id"] for row in page["items"]] == [made[2], made[1]]
    assert page["next_cursor"]
    second = client.get(
        "/v1/requisitions",
        params={"brand": brand, "limit": 2, "cursor": page["next_cursor"]},
        headers=headers,
    ).json()
    assert [row["id"] for row in second["items"]] == [made[0]]
    assert second["next_cursor"] is None

    bad = client.get("/v1/requisitions", params={"cursor": "nope"}, headers=headers)
    assert bad.status_code == 400
    assert _error(bad)["code"] == "invalid_cursor"


def test_reference_reasons_come_from_the_list_in_force(api):
    client, _connection = api
    headers = _as(client, "recruiter-a")
    rejection = client.get("/v1/reference/reasons", params={"kind": "rejection"}, headers=headers)
    assert "withdrew_other" in {reason["code"] for reason in rejection.json()["items"]}
    override = client.get("/v1/reference/reasons", params={"kind": "override"}, headers=headers)
    assert override.json()["items"] == []


def test_an_archived_candidates_application_leaves_the_working_list(api_client, make_candidate):
    """Archiving puts a record away with a reason (BR-205). An application nobody should be
    working is not part of a working list, so the filter follows the candidate."""
    client, connection = api_client
    headers = sign_in(client, "ta-lead")
    opening = client.post(
        "/v1/requisitions",
        json={
            "brand": "Made Up Brand",
            "department": "Sales",
            "track": "A",
            "headcount": 1,
            "team": "team-a",
            "title": "Sales Agent",
        },
        headers={**headers, "Idempotency-Key": str(uuid.uuid4())},
    ).json()

    applications = {}
    for name in ("Stays In Play", "Put Away"):
        candidate = make_candidate(connection, full_name=name)
        answer = client.post(
            "/v1/applications",
            json={"requisition_id": opening["id"], "candidate_id": f"cand_{candidate}"},
            headers={**headers, "Idempotency-Key": str(uuid.uuid4())},
        )
        assert answer.status_code == 201, answer.text
        applications[name] = (candidate, answer.json()["id"])

    put_away, hidden = applications["Put Away"]
    archive_candidate(connection, put_away, "A test record, not a person", "integration-test")

    working = {
        row["id"]
        for row in client.get(
            "/v1/applications", params={"archived": "false"}, headers=headers
        ).json()["items"]
    }
    assert applications["Stays In Play"][1] in working
    assert hidden not in working

    # Asked for, they are still there: archiving hides nothing from someone looking for it.
    every = {row["id"] for row in client.get("/v1/applications", headers=headers).json()["items"]}
    assert hidden in every


def _with_careers(app_engine, careers_url: str = ""):
    connection = app_engine.connect()

    @contextmanager
    def transaction():
        with connection.begin_nested():
            yield connection

    settings = Settings(
        _env_file=None,
        env="dev",
        auth_mode="dev",
        careers_url=careers_url,
        db_dsn="postgresql+psycopg://unused:unused@localhost:1/unused",
        redis_url="redis://localhost:1/0",
        blob_endpoint="http://localhost:1",
        blob_access_key="unused",
        blob_secret_key="unused",
    )
    return TestClient(create_app(settings, probes={}, transaction=transaction)), connection


def test_a_job_post_comes_back_as_a_link_somebody_can_publish(app_engine):
    """The code is what the platform needs; the link is what a person needs (BR-602)."""
    client, connection = _with_careers(app_engine, "https://careers.example.com/jobs")
    try:
        headers = _as(client, "ta-lead")
        requisition = _requisition(client, headers, title="Sales Agent")
        made = client.post(
            f"/v1/requisitions/{requisition['id']}/job-posts",
            json={"channel": "tiktok", "label": "TikTok bio"},
            headers={**headers, "Idempotency-Key": str(uuid.uuid4())},
        ).json()

        expected = (
            f"https://careers.example.com/jobs?job={requisition['id']}&code={made['tracking_code']}"
        )
        assert made["url"] == expected
        # The same link when it is read back, not one every caller has to build for itself.
        listed = client.get(
            f"/v1/requisitions/{requisition['id']}/job-posts", headers=headers
        ).json()
        assert listed["items"][0]["url"] == expected
    finally:
        connection.rollback()
        connection.close()


def test_without_a_careers_address_a_job_post_still_has_its_code(app_engine):
    """The page belongs to the website team. Until we know where it is, the code is the answer."""
    client, connection = _with_careers(app_engine)
    try:
        headers = _as(client, "ta-lead")
        requisition = _requisition(client, headers)
        made = client.post(
            f"/v1/requisitions/{requisition['id']}/job-posts",
            json={"channel": "linkedin"},
            headers={**headers, "Idempotency-Key": str(uuid.uuid4())},
        ).json()
        assert made["url"] is None
        assert made["tracking_code"].startswith("linkedin-")
    finally:
        connection.rollback()
        connection.close()
