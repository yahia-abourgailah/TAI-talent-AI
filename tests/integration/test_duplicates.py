"""The same person found twice, and what a person does about it (BR-203, BR-204, BR-206).

The matcher finds matches and opens review items; nothing joins by itself. A person joins, which
resolves the item, and can undo it. Made-up people; everything rolls back.
"""

import uuid

import pytest
from sqlalchemy import text

from candidates import joins
from candidates.duplicates import find_pairs, record
from pipeline.access import Actor, Refused

from .conftest import sign_in

TA_LEAD = Actor("dev|ta-lead", sees_all=True)
# Fabricated numbers and addresses that belong to no one.
MOBILE = "010" + "00000031"


@pytest.fixture
def conn(app_engine):
    with app_engine.connect() as connection:
        yield connection


def _scan(conn) -> dict[str, int]:
    pairs, _too_common = find_pairs(
        [
            (int(row.candidate_id), row.field, row.value)
            for row in conn.execute(
                text(
                    "SELECT candidate_id, field, value FROM core.candidate_field_current "
                    "WHERE field = ANY(ARRAY['full_name','phone','whatsapp','email',"
                    "'profile_url']) "
                    "AND value IS NOT NULL"
                )
            )
        ]
    )
    return record(conn, pairs)


@pytest.fixture
def twins(conn, make_candidate):
    """Two records of one made-up person: the same number, written differently."""
    first = make_candidate(conn, full_name="Made Up Person", phone=MOBILE)
    second = make_candidate(conn, full_name="Made-up  PERSON", whatsapp="+20 100 000 0031")
    _scan(conn)
    match = conn.execute(
        text(
            "SELECT id, strength, evidence FROM core.candidate_match "
            "WHERE lower_id = :a AND higher_id = :b"
        ),
        {"a": min(first, second), "b": max(first, second)},
    ).one()
    return first, second, match


def test_two_records_of_one_person_are_matched_and_wait_for_a_person(conn, twins):
    first, second, match = twins
    assert match.strength == "strong"
    assert "phone" in match.evidence

    item = conn.execute(
        text(
            "SELECT kind, candidate_id, reason_code, proposed_by FROM pipeline.review_item "
            "WHERE match_id = :match"
        ),
        {"match": match.id},
    ).one()
    assert (item.kind, item.reason_code) == ("possible_duplicate", "possible_duplicate")
    assert item.candidate_id == min(first, second)
    # Nothing is joined by the matcher (BR-206).
    joined = conn.execute(
        text("SELECT count(*) FROM core.candidate_join WHERE joined_id IN (:a, :b)"),
        {"a": first, "b": second},
    ).scalar_one()
    assert joined == 0


def test_scanning_again_adds_nothing(conn, twins):
    again = _scan(conn)
    assert again["matches_new"] == 0
    assert again["review_items_opened"] == 0


def test_a_person_joins_the_records_and_can_undo_it(conn, twins):
    first, second, match = twins
    join = joins.join(conn, TA_LEAD, first, second, "Same number and name", int(match.id))
    assert (join["primary_id"], join["joined_id"]) == (first, second)

    group = joins.group_of(conn, TA_LEAD, second)
    assert group == {"primary_id": first, "members": sorted([first, second])}

    resolution = conn.execute(
        text(
            "SELECT x.outcome, x.resolved_by FROM pipeline.review_resolution x "
            "JOIN pipeline.review_item r ON r.id = x.review_item_id WHERE r.match_id = :match"
        ),
        {"match": match.id},
    ).one()
    assert (resolution.outcome, resolution.resolved_by) == ("checked", TA_LEAD.subject)

    undone = joins.undo(conn, TA_LEAD, int(join["id"]), "Different people after all")
    assert undone["undone_by"] == TA_LEAD.subject
    assert joins.group_of(conn, TA_LEAD, second)["primary_id"] == second
    with pytest.raises(Refused, match="already undone"):
        joins.undo(conn, TA_LEAD, int(join["id"]), "Again")

    # Both records kept everything they had.
    for candidate in (first, second):
        fields = conn.execute(
            text("SELECT count(*) FROM core.candidate_field_current WHERE candidate_id = :c"),
            {"c": candidate},
        ).scalar_one()
        assert fields >= 2


def test_a_group_is_only_ever_one_level_deep(conn, twins, make_candidate):
    first, second, _match = twins
    joins.join(conn, TA_LEAD, first, second, "Same person")
    third = make_candidate(conn, full_name="Another Made Up")
    with pytest.raises(Refused, match="joined into another record"):
        joins.join(conn, TA_LEAD, second, third, "Joining into a joined record")
    with pytest.raises(Refused, match="cannot be joined"):
        joins.join(conn, TA_LEAD, third, first, "Joining a record others are joined into")


def test_a_record_is_joined_once_at_a_time(conn, twins, make_candidate):
    first, second, _match = twins
    joins.join(conn, TA_LEAD, first, second, "Same person")
    third = make_candidate(conn, full_name="Third Made Up")
    with pytest.raises(Refused, match="already exists"):
        joins.join(conn, TA_LEAD, third, second, "Joined somewhere else already")


def test_a_reviewer_sees_the_match_and_joins_through_the_api(api_client, make_candidate):
    client, connection = api_client
    first = make_candidate(connection, full_name="Api Made Up", email="api.made.up@example.com")
    second = make_candidate(connection, full_name="Api Made-Up", email="API.Made.Up@example.com")
    _scan(connection)

    headers = sign_in(client, "ta-lead")
    listed = client.get(
        "/v1/candidate-review-items", params={"kind": "possible_duplicate"}, headers=headers
    )
    assert listed.status_code == 200, listed.text
    items = [
        item
        for item in listed.json()["items"]
        if item["candidate_id"] == f"cand_{min(first, second)}"
    ]
    assert len(items) == 1
    item = items[0]
    assert item["match"]["other_candidate_id"] == f"cand_{max(first, second)}"
    assert item["match"]["strength"] == "strong"
    assert "email" in item["match"]["evidence"]

    joined = client.post(
        f"/v1/candidates/cand_{first}/joins",
        json={"joined_candidate_id": f"cand_{second}", "reason": "Same email"},
        headers={**headers, "Idempotency-Key": str(uuid.uuid4())},
    )
    assert joined.status_code == 201, joined.text
    assert joined.json()["id"].startswith("jon_")

    group = client.get(f"/v1/candidates/cand_{second}/group", headers=headers).json()
    assert group["primary_candidate_id"] == f"cand_{first}"
    assert sorted(group["members"]) == sorted([f"cand_{first}", f"cand_{second}"])

    resolved = client.get(
        "/v1/candidate-review-items", params={"status": "resolved"}, headers=headers
    ).json()
    assert item["id"] in {row["id"] for row in resolved["items"]}

    undone = client.post(
        f"/v1/candidate-joins/{joined.json()['id']}/undo",
        json={"reason": "Checked again: two people"},
        headers=headers,
    )
    assert undone.status_code == 201, undone.text
    after = client.get(f"/v1/candidates/cand_{second}/group", headers=headers).json()
    assert after["primary_candidate_id"] == f"cand_{second}"


def test_a_recruiter_cannot_join_records_they_cannot_see(api_client, make_candidate):
    client, connection = api_client
    first = make_candidate(connection, full_name="Hidden Made Up", phone=MOBILE)
    second = make_candidate(connection, full_name="Hidden Made-Up", whatsapp=MOBILE)
    _scan(connection)
    response = client.post(
        f"/v1/candidates/cand_{first}/joins",
        json={"joined_candidate_id": f"cand_{second}", "reason": "Same number"},
        headers={
            **sign_in(client, "recruiter-a"),
            "Idempotency-Key": str(uuid.uuid4()),
        },
    )
    assert response.status_code == 404
