"""Events for the CRM (migration 0007): written by the database, read through the feed, delivered
by webhook with retries. Made-up candidates; every test rolls back.
"""

import json
import time
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from api.app import create_app
from config import Settings
from integrations.events import list_events
from integrations.webhooks import deliver_next, delivery_status, verify
from pipeline.access import Actor
from pipeline.dev_candidate import create_demo_candidate
from pipeline.store import create_application, create_opening, move_application, propose_rejection

RECRUITER_A = Actor("dev|recruiter-a", sees_all=False)
RECRUITER_B = Actor("dev|recruiter-b", sees_all=False)
TA_LEAD = Actor("dev|ta-lead", sees_all=True)
URL = "https://crm.example.com/hooks/talent"
SECRET = "test-webhook-secret-0123456789abcdef"


@pytest.fixture
def conn(app_engine):
    with app_engine.connect() as connection:
        yield connection


@pytest.fixture
def owner_conn(owner_engine):
    with owner_engine.connect() as connection:
        yield connection


def _application(conn, actor: Actor = RECRUITER_A) -> dict:
    opening = create_opening(
        conn, actor, brand="Made-up Brand", department="Sales", track="A", headcount=1, team="t"
    )
    candidate = create_demo_candidate(conn, "integration-test", source="test-events")
    return create_application(conn, actor, opening["id"], candidate)


def _events(conn, application_id: int) -> list:
    return conn.execute(
        text("SELECT id, type, data FROM integration.event WHERE application_id = :id ORDER BY id"),
        {"id": application_id},
    ).all()


def _sqlstate(error) -> str:
    return error.value.orig.sqlstate


def test_a_new_application_and_each_move_become_events(conn):
    application = _application(conn)
    move_application(conn, RECRUITER_A, application["id"], from_step="new", to_step="contacted")
    events = _events(conn, application["id"])
    assert [event.type for event in events] == ["application.stage_changed"] * 2
    first, second = (event.data for event in events)
    assert first["application_id"] == f"app_{application['id']}"
    assert first["candidate_id"] == f"cand_{application['candidate_id']}"
    assert first["requisition_id"] == f"req_{application['opening_id']}"
    assert first["transition_id"].startswith("trn_")
    assert (first["from_stage"], first["to_stage"]) == (None, "new")
    assert (second["from_stage"], second["to_stage"]) == ("new", "contacted")


def test_a_proposed_rejection_is_a_review_event(conn):
    application = _application(conn)
    item = propose_rejection(conn, application["id"], "no_response", "integration-test")
    (event,) = [e for e in _events(conn, application["id"]) if e.type == "review.item_created"]
    assert event.data == {
        "review_item_id": f"rvw_{item}",
        "kind": "negative_verdict",
        "candidate_id": f"cand_{application['candidate_id']}",
        "application_id": f"app_{application['id']}",
    }


def test_only_a_computed_evaluation_is_a_scored_event(conn):
    candidate = create_demo_candidate(conn, "integration-test", source="test-events")
    insert = text(
        "INSERT INTO core.evaluation "
        "(candidate_id, criteria_version_id, origin, score, tier, evaluated_at, recorded_by, "
        " flags_text) "
        "VALUES (:c, '2026-08-04', :origin, :score, :tier, clock_timestamp(), 'integration-test', "
        " :flags) RETURNING id"
    )
    conn.execute(
        insert, {"c": candidate, "origin": "stored", "score": 60, "tier": "P3", "flags": None}
    )
    passed = conn.execute(
        insert, {"c": candidate, "origin": "computed", "score": 61, "tier": "P3", "flags": None}
    ).scalar_one()
    failed = conn.execute(
        insert,
        {
            "c": candidate,
            "origin": "computed",
            "score": 0,
            "tier": "P4",
            "flags": "DISQUALIFIED: x",
        },
    ).scalar_one()
    scored = (
        conn.execute(
            text(
                "SELECT data FROM integration.event WHERE type = 'candidate.scored' "
                "AND data->>'candidate_id' = :c ORDER BY id"
            ),
            {"c": f"cand_{candidate}"},
        )
        .scalars()
        .all()
    )
    assert scored == [
        {
            "candidate_id": f"cand_{candidate}",
            "evaluation_id": f"evl_{passed}",
            "criteria_version": "2026-08-04",
            "outcome": "passed",
            "tier": "P3",
        },
        {
            "candidate_id": f"cand_{candidate}",
            "evaluation_id": f"evl_{failed}",
            "criteria_version": "2026-08-04",
            "outcome": "failed_gate",
            "tier": "P4",
        },
    ]


def test_nobody_writes_or_changes_an_event_by_hand(conn, owner_conn):
    with pytest.raises(DBAPIError) as error, conn.begin_nested():
        conn.execute(
            text(
                "INSERT INTO integration.event (type, occurred_at, data) "
                "VALUES ('candidate.scored', now(), '{}')"
            )
        )
    assert _sqlstate(error) == "42501"

    application = _application(owner_conn)
    (event_id, *_rest) = _events(owner_conn, application["id"])[0]
    for statement in (
        "UPDATE integration.event SET data = '{}' WHERE id = :id",
        "DELETE FROM integration.event WHERE id = :id",
    ):
        with pytest.raises(DBAPIError) as error, owner_conn.begin_nested():
            owner_conn.execute(text(statement), {"id": event_id})
        assert _sqlstate(error) == "42501"


def test_the_feed_is_scoped_and_resumes_after_an_event(conn):
    mine = _application(conn, RECRUITER_A)
    theirs = _application(conn, RECRUITER_B)
    move_application(conn, RECRUITER_A, mine["id"], from_step="new", to_step="contacted")

    def ids(actor, **kwargs):
        rows = list_events(conn, actor, limit=1000, **kwargs)
        return [(row["id"], row["data"].get("application_id")) for row in rows]

    seen_by_a = ids(RECRUITER_A)
    assert f"app_{mine['id']}" in {app for _, app in seen_by_a}
    assert f"app_{theirs['id']}" not in {app for _, app in seen_by_a}
    assert {f"app_{mine['id']}", f"app_{theirs['id']}"} <= {app for _, app in ids(TA_LEAD)}

    first_of_mine = min(event for event, app in seen_by_a if app == f"app_{mine['id']}")
    later = ids(RECRUITER_A, after=first_of_mine)
    assert later and all(event > first_of_mine for event, _ in later)

    propose_rejection(conn, mine["id"], "no_response", "integration-test")
    reviews = list_events(conn, RECRUITER_A, limit=1000, types=["review.item_created"])
    assert reviews
    assert {row["type"] for row in reviews} == {"review.item_created"}


def _client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def _event_id(conn) -> int:
    application = _application(conn)
    return int(_events(conn, application["id"])[0].id)


def test_an_event_is_delivered_once_signed(conn):
    event_id = _event_id(conn)
    received: list[httpx.Request] = []

    def crm(request: httpx.Request) -> httpx.Response:
        received.append(request)
        return httpx.Response(202)

    with _client(crm) as client:
        attempt = deliver_next(conn, client, URL, SECRET, event_id=event_id)
        assert attempt is not None
        assert (attempt.event_id, attempt.attempt, attempt.outcome) == (
            f"evt_{event_id}",
            1,
            "delivered",
        )
        assert deliver_next(conn, client, URL, SECRET, event_id=event_id) is None

    (request,) = received
    body = request.content
    assert request.headers["x-talent-event-id"] == f"evt_{event_id}"
    assert verify(
        SECRET,
        request.headers["x-talent-timestamp"],
        body,
        request.headers["x-talent-signature"],
        now=time.time(),
    )
    event = json.loads(body)
    assert set(event) == {"id", "type", "api_version", "occurred_at", "data"}
    assert event["type"] == "application.stage_changed"


def test_a_failed_delivery_waits_then_retries_and_parks_after_a_day(conn):
    event_id = _event_id(conn)
    answers = iter([httpx.Response(503), httpx.Response(200)])

    with _client(lambda _request: next(answers)) as client:
        first = deliver_next(conn, client, URL, SECRET, event_id=event_id)
        assert first is not None
        assert (first.outcome, first.status_code, first.failure) == ("failed", 503, "http_status")
        assert deliver_next(conn, client, URL, SECRET, event_id=event_id) is None  # not due yet

        soon = datetime.now(UTC) + timedelta(seconds=61)
        second = deliver_next(conn, client, URL, SECRET, now=soon, event_id=event_id)
        assert second is not None
        assert (second.attempt, second.outcome) == (2, "delivered")

    parked_id = _event_id(conn)
    tomorrow = datetime.now(UTC) + timedelta(hours=25)
    with _client(lambda _request: httpx.Response(200)) as client:
        assert deliver_next(conn, client, URL, SECRET, now=tomorrow, event_id=parked_id) is None
    assert delivery_status(conn, now=tomorrow)["parked"] >= 1


def test_a_timeout_is_a_failed_attempt_with_no_status(conn):
    event_id = _event_id(conn)

    def slow(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("no answer", request=request)

    with _client(slow) as client:
        attempt = deliver_next(conn, client, URL, SECRET, event_id=event_id)
    assert attempt is not None
    assert (attempt.outcome, attempt.status_code, attempt.failure) == ("failed", None, "timeout")


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


def test_the_feed_through_the_api(api):
    client, connection = api
    token = client.post("/dev/token", json={"account": "recruiter-a"}).json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    brand = f"Made-up Brand {uuid.uuid4().hex[:8]}"
    requisition = client.post(
        "/v1/requisitions",
        json={"brand": brand, "department": "Sales", "track": "A", "headcount": 1, "team": "t"},
        headers=headers,
    ).json()
    candidate = create_demo_candidate(connection, "integration-test", source="test-events")
    application = client.post(
        "/v1/applications",
        json={"requisition_id": requisition["id"], "candidate_id": f"cand_{candidate}"},
        headers=headers,
    ).json()

    feed = client.get(
        "/v1/events", params={"type": "application.stage_changed", "limit": 200}, headers=headers
    )
    assert feed.status_code == 200
    mine = [e for e in feed.json()["items"] if e["data"]["application_id"] == application["id"]]
    assert len(mine) == 1
    assert mine[0]["id"].startswith("evt_") and mine[0]["occurred_at"].endswith("Z")

    resumed = client.get("/v1/events", params={"after": mine[0]["id"]}, headers=headers).json()
    assert all(e["id"] != mine[0]["id"] for e in resumed["items"])
    both = client.get("/v1/events", params={"after": mine[0]["id"], "cursor": "x"}, headers=headers)
    malformed = client.get("/v1/events", params={"after": "app_1"}, headers=headers)
    for response in (both, malformed):
        assert response.status_code == 400
