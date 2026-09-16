"""A candidate who asks us to stop keeping their data (BR-504, BR-205).

A TA member records the request; the record locks and reads as not found for everyone but an
admin, everywhere. Nothing is deleted: an admin can lift a request recorded by mistake, and the
fields, applications and evaluations are all still there afterwards. Made-up people; everything
rolls back.
"""

import uuid

import pytest
from sqlalchemy import text

from candidates import withdrawal
from candidates.duplicates import find_pairs
from candidates.reads import SearchCriteria, get_candidate, search_candidates
from pipeline.access import Actor, NotFound, NotPermitted, Refused

from .conftest import sign_in

TA_LEAD = Actor("dev|ta-lead", sees_all=True)
ADMIN = Actor("dev|admin", sees_all=True, is_admin=True)
# A fabricated number that belongs to no one.
MOBILE = "010" + "00000041"


@pytest.fixture
def conn(app_engine):
    with app_engine.connect() as connection:
        yield connection


@pytest.fixture
def asked_to_be_left_alone(conn, make_candidate):
    candidate = make_candidate(conn, full_name="Left Alone Person", phone=MOBILE)
    record = withdrawal.record(
        conn, TA_LEAD, candidate, asked_how="whatsapp", note="Asked us to stop keeping her data"
    )
    return candidate, record


def test_a_locked_record_reads_as_not_found(conn, asked_to_be_left_alone):
    candidate, _record = asked_to_be_left_alone
    with pytest.raises(NotFound):
        get_candidate(conn, TA_LEAD, candidate)
    found = search_candidates(
        conn, TA_LEAD, SearchCriteria.build(full_name=None, email=None, phone=MOBILE), limit=10
    )
    assert [row for row in found if row["id"] == candidate] == []
    # An admin still sees it, which is how a mistake is put right.
    assert get_candidate(conn, ADMIN, candidate)["id"] == candidate
    assert withdrawal.is_locked(conn, candidate)


def test_nothing_about_the_candidate_is_deleted(conn, asked_to_be_left_alone):
    candidate, _record = asked_to_be_left_alone
    fields = conn.execute(
        text("SELECT count(*) FROM core.candidate_field_current WHERE candidate_id = :c"),
        {"c": candidate},
    ).scalar_one()
    assert fields >= 2


def test_a_locked_record_is_matched_against_no_one(conn, asked_to_be_left_alone, make_candidate):
    candidate, _record = asked_to_be_left_alone
    twin = make_candidate(conn, full_name="Left Alone Person", phone=MOBILE)
    identities = [
        (int(row.candidate_id), row.field, row.value)
        for row in conn.execute(
            text(
                "SELECT candidate_id, field, value FROM core.candidate_field_current f "
                "WHERE candidate_id IN (:a, :b) AND value IS NOT NULL "
                "AND NOT EXISTS (SELECT 1 FROM core.candidate_locked lock "
                "                WHERE lock.candidate_id = f.candidate_id)"
            ),
            {"a": candidate, "b": twin},
        )
    ]
    assert find_pairs(identities).pairs == []


def test_only_an_admin_lifts_a_withdrawal(conn, asked_to_be_left_alone):
    candidate, record = asked_to_be_left_alone
    with pytest.raises(NotPermitted):
        withdrawal.lift(conn, TA_LEAD, int(record["id"]), "The TA lead wants the record back")

    lifted = withdrawal.lift(conn, ADMIN, int(record["id"]), "Recorded against the wrong person")
    assert lifted["lifted_by"] == ADMIN.subject
    assert not withdrawal.is_locked(conn, candidate)
    assert get_candidate(conn, TA_LEAD, candidate)["id"] == candidate
    with pytest.raises(Refused, match="already lifted"):
        withdrawal.lift(conn, ADMIN, int(record["id"]), "Again")


def test_the_request_itself_cannot_be_changed_or_removed(conn, asked_to_be_left_alone):
    _candidate, record = asked_to_be_left_alone
    for statement in (
        "UPDATE core.consent_withdrawal SET asked_how = 'email' WHERE id = :id",
        "DELETE FROM core.consent_withdrawal WHERE id = :id",
    ):
        # The database refuses, by trigger or by grant.
        with pytest.raises(Exception), conn.begin_nested():  # noqa: B017
            conn.execute(text(statement), {"id": int(record["id"])})


def test_a_recruiter_records_a_withdrawal_through_the_api(api_client, make_candidate):
    client, connection = api_client
    candidate = make_candidate(connection, full_name="Api Left Alone", phone=MOBILE)
    lead = sign_in(client, "ta-lead")

    recorded = client.post(
        f"/v1/candidates/cand_{candidate}/withdrawals",
        json={"asked_how": "phone", "note": "Called the office and asked us to stop"},
        headers={**lead, "Idempotency-Key": str(uuid.uuid4())},
    )
    assert recorded.status_code == 201, recorded.text
    body = recorded.json()
    assert body["id"].startswith("wdr_")
    assert body["candidate_id"] == f"cand_{candidate}"
    assert body["recorded_by"] == "dev|ta-lead"

    # Gone for everyone but an admin, everywhere.
    assert client.get(f"/v1/candidates/cand_{candidate}", headers=lead).status_code == 404
    listed = client.get("/v1/candidates", headers=lead).json()["items"]
    assert f"cand_{candidate}" not in {row["id"] for row in listed}
    searched = client.post("/v1/candidates/search", json={"phone": MOBILE}, headers=lead)
    assert f"cand_{candidate}" not in {row["id"] for row in searched.json()["items"]}
    assert client.get("/v1/withdrawals", headers=lead).status_code == 403

    admin = sign_in(client, "admin")
    locked = client.get("/v1/withdrawals", headers=admin)
    assert locked.status_code == 200, locked.text
    assert body["id"] in {row["id"] for row in locked.json()["items"]}

    lifted = client.post(
        f"/v1/withdrawals/{body['id']}/lift",
        json={"reason": "Recorded against the wrong record"},
        headers=admin,
    )
    assert lifted.status_code == 201, lifted.text
    assert client.get(f"/v1/candidates/cand_{candidate}", headers=lead).status_code == 200


def test_a_recruiter_cannot_lock_a_record_they_cannot_see(api_client, make_candidate):
    client, connection = api_client
    candidate = make_candidate(connection, full_name="Hidden Left Alone")
    response = client.post(
        f"/v1/candidates/cand_{candidate}/withdrawals",
        json={"asked_how": "email"},
        headers={**sign_in(client, "recruiter-a"), "Idempotency-Key": str(uuid.uuid4())},
    )
    assert response.status_code == 404
