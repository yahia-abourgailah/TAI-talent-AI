"""Candidate search (API plan section 6): values in the body, exact matches, only in scope, and no
personal values in the answer. Made-up people; everything rolls back.
"""

from contextlib import contextmanager

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from api.app import create_app
from config import Settings
from pipeline.dev_candidate import create_demo_candidate

NAME = "Made-up  Person"
EMAIL = "Made.Up@Example.com"
# Fabricated numbers that belong to no one, stored and searched in different forms.
STORED_PHONE = "+20 100 000 " + "0001"
LOCAL_PHONE = "010" + "00000001"
ARABIC_PHONE = "\u0660\u0661\u0660" + "\u0660" * 7 + "\u0661"  # Arabic-Indic digits


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


@pytest.fixture
def candidate(api) -> str:
    client, connection = api
    headers = _as(client, "recruiter-a")
    requisition = client.post(
        "/v1/requisitions",
        json={
            "brand": "Made-up Brand",
            "department": "Sales",
            "track": "A",
            "headcount": 1,
            "team": "t",
        },
        headers=headers,
    ).json()
    number = create_demo_candidate(connection, "integration-test", source="test-search")
    for field, value in (("full_name", NAME), ("email", EMAIL), ("phone", STORED_PHONE)):
        connection.execute(
            text(
                "INSERT INTO core.candidate_field "
                "(candidate_id, field, value, source, verification_status, recorded_by) "
                "VALUES (:c, :field, :value, 'recruiter_entered', 'unverified', 'test')"
            ),
            {"c": number, "field": field, "value": value},
        )
    response = client.post(
        "/v1/applications",
        json={"requisition_id": requisition["id"], "candidate_id": f"cand_{number}"},
        headers=headers,
    )
    assert response.status_code == 201, response.text
    return f"cand_{number}"


def _search(client, account: str, **body):
    return client.post("/v1/candidates/search", json=body, headers=_as(client, account))


@pytest.mark.parametrize(
    "body",
    [
        {"email": "  made.up@example.COM "},
        {"phone": LOCAL_PHONE},
        {"phone": "0020 100 000 0001"},
        {"phone": ARABIC_PHONE},
        {"full_name": "made-up person"},
        {"full_name": "MADE-UP PERSON", "email": "made.up@example.com", "phone": LOCAL_PHONE},
    ],
)
def test_one_person_is_found_however_the_values_are_typed(api, candidate, body):
    client, _connection = api
    response = _search(client, "recruiter-a", **body)
    assert response.status_code == 200, response.text
    assert [item["id"] for item in response.json()["items"]] == [candidate]


def test_every_value_sent_must_match(api, candidate):
    client, _connection = api
    response = _search(client, "recruiter-a", email="made.up@example.com", phone="01099999999")
    assert response.json()["items"] == []
    assert _search(client, "recruiter-a", full_name="Made-up").json()["items"] == []


def test_search_stays_in_scope_and_never_answers_with_personal_values(api, candidate):
    client, _connection = api
    assert _search(client, "recruiter-b", email=EMAIL).json()["items"] == []
    assert [i["id"] for i in _search(client, "ta-lead", email=EMAIL).json()["items"]] == [candidate]
    found = _search(client, "recruiter-a", email=EMAIL)
    for value in ("Made-up", "made.up", "example.com", LOCAL_PHONE[1:]):
        assert value.lower() not in found.text.lower()
    assert _search(client, "criteria-owner", email=EMAIL).status_code == 403


@pytest.mark.parametrize(
    "body", [{}, {"email": "no-at-sign"}, {"phone": "12345678ab"}, {"full_name": "   "}]
)
def test_a_search_without_a_usable_value_is_a_bad_request(api, body):
    client, _connection = api
    response = _search(client, "recruiter-a", **body)
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_request"
