"""B3: a rejected record is archived with its reason and actor, stays readable, is never deleted.

Every test runs inside a transaction that is rolled back. Made-up data only.
"""

import hashlib
import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from candidates.archive import ArchiveError, archive_candidate

from .conftest import sign_in


def _candidate(conn) -> int:
    capture_id = conn.execute(
        text(
            """
            INSERT INTO raw.capture
              (source, external_id, content_sha256, blob_key, media_type, byte_size, received_by)
            VALUES
              ('integration-test', :ext, :sha, :key, 'application/json', 10, 'integration-test')
            RETURNING id
            """
        ),
        {
            "ext": uuid.uuid4().hex,
            "sha": hashlib.sha256(uuid.uuid4().bytes).digest(),
            "key": f"test/{uuid.uuid4().hex}",
        },
    ).scalar_one()
    return int(
        conn.execute(
            text(
                "INSERT INTO core.candidate (capture_id, source_key, created_by, pipeline_state) "
                "VALUES (:c, :k, 'integration-test', 'not_recorded') RETURNING id"
            ),
            {"c": capture_id, "k": f"test:{uuid.uuid4().hex}"},
        ).scalar_one()
    )


def _sqlstate(error: pytest.ExceptionInfo[DBAPIError]) -> str:
    return error.value.orig.sqlstate  # type: ignore[union-attr]


def test_a_rejected_row_is_archived_with_its_reason_and_still_readable(app_engine):
    with app_engine.connect() as conn:
        candidate_id = _candidate(conn)
        archive_candidate(conn, candidate_id, "Test data, not a candidate (Q-13)", "reviewer-a")
        row = conn.execute(
            text(
                "SELECT source_key, archived_at, archived_reason, archived_by "
                "FROM core.candidate WHERE id = :id"
            ),
            {"id": candidate_id},
        ).one()
    assert row.source_key.startswith("test:")
    assert row.archived_at is not None
    assert (row.archived_reason, row.archived_by) == (
        "Test data, not a candidate (Q-13)",
        "reviewer-a",
    )


@pytest.mark.parametrize(("reason", "actor"), [("", "reviewer-a"), ("Duplicate", "  ")])
def test_an_archive_needs_a_reason_and_a_person(app_engine, reason, actor):
    with app_engine.connect() as conn:
        candidate_id = _candidate(conn)
        with pytest.raises(ArchiveError):
            archive_candidate(conn, candidate_id, reason, actor)


def test_archiving_twice_is_refused(app_engine):
    with app_engine.connect() as conn:
        candidate_id = _candidate(conn)
        archive_candidate(conn, candidate_id, "Duplicate capture", "reviewer-a")
        with pytest.raises(ArchiveError, match="already archived"):
            archive_candidate(conn, candidate_id, "Another reason", "reviewer-b")


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE core.candidate SET archived_at = now() WHERE id = :id",
        "UPDATE core.candidate SET archived_at = now(), archived_reason = E'\\t', "
        "archived_by = 'reviewer-a' WHERE id = :id",
    ],
)
def test_the_database_refuses_an_archive_without_a_real_reason(app_engine, statement):
    with app_engine.connect() as conn:
        candidate_id = _candidate(conn)
        with pytest.raises(DBAPIError) as error:
            conn.execute(text(statement), {"id": candidate_id})
    assert _sqlstate(error) == "23514"


def test_even_the_owner_cannot_change_or_clear_an_archive(owner_engine):
    with owner_engine.connect() as conn:
        candidate_id = _candidate(conn)
        archive_candidate(conn, candidate_id, "Duplicate capture", "reviewer-a")
        with pytest.raises(DBAPIError) as error:
            conn.execute(
                text(
                    "UPDATE core.candidate SET archived_at = NULL, archived_reason = NULL, "
                    "archived_by = NULL WHERE id = :id"
                ),
                {"id": candidate_id},
            )
    assert "permanent" in str(error.value.orig)


def test_the_app_cannot_delete_an_archived_candidate(app_engine):
    with app_engine.connect() as conn:
        candidate_id = _candidate(conn)
        archive_candidate(conn, candidate_id, "Duplicate capture", "reviewer-a")
        with pytest.raises(DBAPIError) as error:
            conn.execute(text("DELETE FROM core.candidate WHERE id = :id"), {"id": candidate_id})
    assert _sqlstate(error) == "42501"


def test_a_list_can_leave_out_what_was_put_away(api_client, make_candidate):
    """Archiving takes a record out of the way (BR-205). A list that still shows it has not."""
    client, connection = api_client
    headers = sign_in(client, "ta-lead")
    kept = f"cand_{make_candidate(connection, full_name='Still In Play')}"
    put_away = make_candidate(connection, full_name="A Test Upload, Not A Person")
    archive_candidate(connection, put_away, "Made-up file from a test run", "integration-test")

    in_play = {
        row["id"]
        for row in client.get(
            "/v1/candidates", params={"archived": "false"}, headers=headers
        ).json()["items"]
    }
    assert kept in in_play
    assert f"cand_{put_away}" not in in_play

    archived = client.get("/v1/candidates", params={"archived": "true"}, headers=headers).json()
    assert f"cand_{put_away}" in {row["id"] for row in archived["items"]}
    assert all(row["archived"] for row in archived["items"])

    # Nothing is lost: the record still reads, with the reason and who did it.
    one = client.get(f"/v1/candidates/cand_{put_away}", headers=headers).json()
    assert one["archived_reason"] == "Made-up file from a test run"


def test_archiving_takes_the_candidate_out_of_the_review_queue(api_client, make_candidate):
    """A queue is a list of work. Work on a record that is out of play leads nowhere (BR-205)."""
    client, connection = api_client
    headers = sign_in(client, "ta-lead")
    typed_in = client.post(
        "/v1/candidates",
        json={"fields": {"full_name": "A Test Upload, Not A Person", "phone": "01000000000"}},
        headers={**headers, "Idempotency-Key": str(uuid.uuid4())},
    ).json()
    candidate = int(typed_in["id"].removeprefix("cand_"))

    def waiting() -> set[str]:
        items = client.get("/v1/review-queue", params={"limit": 100}, headers=headers).json()
        return {row["candidate_id"] for row in items["items"]}

    def waiting_about_candidates() -> set[str]:
        items = client.get(
            "/v1/candidate-review-items", params={"limit": 100}, headers=headers
        ).json()
        return {row["candidate_id"] for row in items["items"]}

    assert typed_in["id"] in waiting(), "a typed-in candidate waits to be checked"
    assert typed_in["id"] in waiting_about_candidates()

    archive_candidate(connection, candidate, "Made-up file from a test run", "integration-test")

    assert typed_in["id"] not in waiting()
    assert typed_in["id"] not in waiting_about_candidates()
    # The item itself is not resolved, and nothing is deleted: it is out of the way, not gone.
    assert (
        connection.execute(
            text("SELECT count(*) FROM pipeline.review_item WHERE candidate_id = :id"),
            {"id": candidate},
        ).scalar_one()
        >= 1
    )


def test_putting_the_test_data_away_archives_what_we_made_and_keeps_the_import(
    api_client, make_candidate
):
    """One button for what a week of trying things leaves behind (BR-205, BR-401)."""
    client, connection = api_client
    headers = sign_in(client, "ta-lead")
    ours = make_candidate(connection, full_name="Console Test Person")
    imported = make_candidate(connection, full_name="From The Sheet")
    connection.execute(
        text(
            "INSERT INTO core.candidate_field "
            "(candidate_id, field, value, source, verification_status, recorded_by) "
            "VALUES (:c, 'full_name', 'From The Sheet', 'migrated from TAI_Master', "
            "'unverified', 'test')"
        ),
        {"c": imported},
    )
    requisition = client.post(
        "/v1/requisitions",
        json={
            "brand": "The Address",
            "department": "Sales",
            "track": "A",
            "headcount": 1,
            "team": "team-a",
            "title": "Sales Agent",
        },
        headers={**headers, "Idempotency-Key": str(uuid.uuid4())},
    ).json()

    done = client.post("/dev/clear-test-data").json()
    assert done["candidates_archived"] >= 1
    assert done["requisitions_closed"] >= 1

    assert client.get(f"/v1/candidates/cand_{ours}", headers=headers).json()["archived_at"]
    assert client.get(f"/v1/candidates/cand_{imported}", headers=headers).json()["archived_at"] is (
        None
    ), "the import is not test data"
    assert (
        client.get(f"/v1/requisitions/{requisition['id']}", headers=headers).json()["status"]
        == "closed"
    )
    # Nothing was deleted: both records still read, with the reason and who did it.
    put_away = client.get(f"/v1/candidates/cand_{ours}", headers=headers).json()
    assert put_away["archived_reason"] == "Made while trying the platform out (dev)"
