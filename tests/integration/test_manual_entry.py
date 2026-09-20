"""Week 5, B4: a recruiter types in a candidate; a person checks the fields (BR-103, BR-202,
BR-201). Made-up values; everything rolls back.
"""

import hashlib
import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from .conftest import sign_in

ENTRY = {
    "fields": {
        "full_name": "مرشح يدوي تجريبي",
        "phone": "+20 122 000 0000",
        "current_title": "Sales Agent",
        "age": "24",
        "email": "",
    }
}


def _enter(client, account: str = "recruiter-a", body: dict | None = None, **headers: str):
    return client.post(
        "/v1/candidates", json=body or ENTRY, headers={**sign_in(client, account), **headers}
    )


def _number(candidate_id: str) -> int:
    return int(candidate_id.removeprefix("cand_"))


def _rows(connection, candidate: int, field: str):
    return connection.execute(
        text(
            "SELECT value, source, verification_status, verified_by, recorded_by "
            "FROM core.candidate_field WHERE candidate_id = :c AND field = :f ORDER BY id"
        ),
        {"c": candidate, "f": field},
    ).all()


def test_a_typed_in_candidate_is_marked_manual_and_unchecked(api_client):
    client, connection = api_client
    response = _enter(client)
    assert response.status_code == 201, response.text
    body = response.json()
    assert set(body) == {"id", "source", "created_at", "archived", "review_item_id"}
    assert body["source"] == "manual_entry"
    assert "مرشح" not in response.text  # the response carries ids only

    me = sign_in(client, "recruiter-a")
    candidate = client.get(f"/v1/candidates/{body['id']}", headers=me).json()
    fields = candidate["fields"]
    assert fields["full_name"] == {
        "value": "مرشح يدوي تجريبي",
        "source": "manual_entry",
        "verification": "unverified",
        "verified_at": None,
        "inference": "stated",
        "language": "ar",
    }
    assert fields["email"] == {"value": None, "state": "not_recorded"}
    assert fields["profile_url"] == {"value": None, "state": "not_recorded"}
    assert _rows(connection, _number(body["id"]), "age")[0].recorded_by == "dev|recruiter-a"

    # The original submission is kept as a raw capture.
    capture = connection.execute(
        text(
            "SELECT r.source, r.received_by FROM core.candidate c "
            "JOIN raw.capture r ON r.id = c.capture_id WHERE c.id = :c"
        ),
        {"c": _number(body["id"])},
    ).one()
    assert tuple(capture) == ("manual_entry", "dev|recruiter-a")

    items = client.get(
        "/v1/candidate-review-items",
        params={"kind": "unverified_candidate", "candidate_id": body["id"]},
        headers=me,
    ).json()["items"]
    assert [(i["id"], i["reason_code"], i["proposed_by"]) for i in items] == [
        (body["review_item_id"], "manual_entry", "dev|recruiter-a")
    ]

    # Only the recruiter who typed them in, and those who see everything, see the candidate.
    for account, expected in {"recruiter-b": 404, "ta-lead": 200, "criteria-owner": 403}.items():
        seen = client.get(f"/v1/candidates/{body['id']}", headers=sign_in(client, account))
        assert seen.status_code == expected, account


def test_checking_a_field_adds_a_verified_row_and_keeps_the_old_one(api_client):
    client, connection = api_client
    candidate_id = _enter(client).json()["id"]
    me = sign_in(client, "recruiter-a")
    path = f"/v1/candidates/{candidate_id}/fields"

    confirmed = client.post(f"{path}/current_title/verification", json={}, headers=me)
    assert confirmed.status_code == 201, confirmed.text
    body = confirmed.json()
    assert (body["verification"], body["verified_by"], body["corrected"], body["source"]) == (
        "verified",
        "dev|recruiter-a",
        False,
        "manual_entry",
    )
    assert "Sales Agent" not in confirmed.text

    corrected = client.post(f"{path}/age/verification", json={"value": "25"}, headers=me)
    assert (corrected.json()["corrected"], corrected.json()["source"]) == (
        True,
        "recruiter_checked",
    )
    history = _rows(connection, _number(candidate_id), "age")
    assert [(r.value, r.verification_status, r.verified_by) for r in history] == [
        ("24", "unverified", None),
        ("25", "verified", "dev|recruiter-a"),
    ]
    shown = client.get(f"/v1/candidates/{candidate_id}", headers=me).json()["fields"]["age"]
    assert (shown["value"], shown["verification"], shown["verified_by"]) == (
        "25",
        "verified",
        "dev|recruiter-a",
    )

    blank = client.post(f"{path}/email/verification", json={}, headers=me)
    assert (blank.status_code, blank.json()["error"]["code"]) == (409, "field_not_recorded")
    filled = client.post(f"{path}/email/verification", json={"value": "a@example.com"}, headers=me)
    assert filled.status_code == 201
    unknown = client.post(f"{path}/salary/verification", json={}, headers=me)
    assert unknown.status_code == 400
    other = client.post(f"{path}/age/verification", json={}, headers=sign_in(client, "recruiter-b"))
    assert other.status_code == 404


def test_the_candidate_is_checked_once_every_field_is(api_client):
    client, connection = api_client
    entered = _enter(client).json()
    me = sign_in(client, "recruiter-a")
    item_path = f"/v1/candidate-review-items/{entered['review_item_id']}"

    early = client.post(f"{item_path}/resolution", json={"decision": "checked"}, headers=me)
    assert (early.status_code, early.json()["error"]["code"]) == (409, "candidate_not_checked")

    path = f"/v1/candidates/{entered['id']}/fields"
    last = None
    for field in ("full_name", "phone", "current_title", "age"):
        last = client.post(f"{path}/{field}/verification", json={}, headers=me).json()
        assert last["resolved_review_item_id"] in (None, entered["review_item_id"])
    assert last["resolved_review_item_id"] == entered["review_item_id"]

    item = client.get(item_path, headers=me).json()
    assert (item["resolution"]["decision"], item["resolution"]["resolved_by"]) == (
        "checked",
        "dev|recruiter-a",
    )
    # A dismissal with a reason is the other way to close one.
    second = _enter(client, body={"fields": {"full_name": "Test Person Two"}}).json()
    dismissed = client.post(
        f"/v1/candidate-review-items/{second['review_item_id']}/resolution",
        json={"decision": "dismiss", "reason": "Entered twice by mistake"},
        headers=me,
    )
    assert dismissed.json()["resolution"]["decision"] == "dismissed"
    signals = connection.execute(
        text("SELECT count(*) FROM pipeline.override_signal WHERE application_id IS NULL")
    ).scalar_one()
    assert signals == 0  # not a criteria signal


def test_bad_entries_are_refused_without_repeating_them(api_client):
    client, _connection = api_client
    empty = _enter(client, body={"fields": {"full_name": "  "}})
    assert (empty.status_code, empty.json()["error"]["code"]) == (400, "invalid_request")
    unknown = _enter(client, body={"fields": {"salary": "Fake Person Name"}})
    assert unknown.status_code == 400
    assert "Fake Person" not in unknown.text
    nothing = _enter(client, body={"fields": {}})
    assert nothing.status_code == 400
    assert _enter(client, "criteria-owner").status_code == 403


def test_a_retried_entry_with_the_same_key_creates_one_candidate(api_client):
    client, _connection = api_client
    key = str(uuid.uuid4())
    first = _enter(client, **{"Idempotency-Key": key})
    again = _enter(client, **{"Idempotency-Key": key})
    assert first.status_code == again.status_code == 201
    assert again.headers["idempotent-replayed"] == "true"
    assert first.json() == again.json()


def test_a_person_can_check_a_field_that_came_from_the_sheet(api_client, owner_engine):
    """The importer writes each field once. That rule was written for the importer, and it caught
    a person: confirming a migrated value looked to the database like a second import, so nobody
    could check any of the 66,820 fields that came across (migration 0017)."""
    client, connection = api_client
    capture = connection.execute(
        text(
            "INSERT INTO raw.capture (source, external_id, content_sha256, blob_key, media_type, "
            "byte_size, received_by) VALUES ('tai_master', :external, :hash, :key, "
            "'application/json', 10, 'integration-test') RETURNING id"
        ),
        {
            "external": f"sheet-row-{uuid.uuid4().hex[:8]}",
            "hash": hashlib.sha256(uuid.uuid4().bytes).digest(),
            "key": f"made-up/sheet-{uuid.uuid4().hex[:8]}",
        },
    ).scalar_one()
    candidate = connection.execute(
        text(
            "INSERT INTO core.candidate (capture_id, source_key, created_by, pipeline_state) "
            "VALUES (:capture, :key, 'integration-test', 'not_recorded') RETURNING id"
        ),
        {"capture": capture, "key": f"tai_master:{capture}"},
    ).scalar_one()
    connection.execute(
        text(
            "INSERT INTO core.candidate_field (candidate_id, field, value, source, "
            "verification_status, recorded_by) VALUES (:c, 'full_name', 'Made Up From The Sheet', "
            "'migrated from TAI_Master', 'unverified', 'integration-test')"
        ),
        {"c": candidate},
    )

    checked = client.post(
        f"/v1/candidates/cand_{candidate}/fields/full_name/verification",
        json={"value": None},
        headers={**sign_in(client, "ta-lead"), "Idempotency-Key": str(uuid.uuid4())},
    )
    assert checked.status_code == 201, checked.text
    assert checked.json()["verification"] == "verified"
    # Where the value came from is unchanged — it did come from the sheet — and the old row stays.
    assert checked.json()["source"] == "migrated from TAI_Master"
    rows = (
        connection.execute(
            text(
                "SELECT verification_status FROM core.candidate_field WHERE candidate_id = :c "
                "AND field = 'full_name' ORDER BY id"
            ),
            {"c": candidate},
        )
        .scalars()
        .all()
    )
    assert rows == ["unverified", "verified"]

    # And the importer still cannot write the same field twice.
    with pytest.raises(DBAPIError), connection.begin_nested():
        connection.execute(
            text(
                "INSERT INTO core.candidate_field (candidate_id, field, value, source, "
                "verification_status, recorded_by) VALUES (:c, 'full_name', 'Imported Again', "
                "'migrated from TAI_Master', 'unverified', 'integration-test')"
            ),
            {"c": candidate},
        )
