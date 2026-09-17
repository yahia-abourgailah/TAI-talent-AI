"""Erasing a candidate when we may no longer keep them (CR-03), and never before (BR-205).

The policy is loaded and activated inside each test and rolled back, so no period is ever in force
outside these tests. Made-up people only.
"""

import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from ops.retention import RetentionError, due, erase_one, in_force, load_policy, summarise

PROPOSED = (
    Path(__file__).resolve().parents[2] / "docs" / "retention" / "proposed-2026-09-17.json"
)
LOADED_BY = "integration-test"


@pytest.fixture
def owner(owner_engine):
    """Erasure runs as the owner, which is the only role that may."""
    with owner_engine.connect() as connection:
        yield connection


@pytest.fixture
def policy(owner):
    body = json.loads(PROPOSED.read_text(encoding="utf-8"))
    body["version"] = "test-policy"
    load_policy(owner, body, LOADED_BY, activate=True)
    return body["version"]


def _candidate(conn, *, months_ago: int, fields: int = 3) -> int:
    """A made-up candidate whose last activity was a while ago."""
    when = datetime.now(UTC) - timedelta(days=31 * months_ago)
    capture = conn.execute(
        text(
            "INSERT INTO raw.capture (source, external_id, content_sha256, blob_key, media_type, "
            "byte_size, received_by) VALUES ('manual_entry', :external, :hash, :key, "
            "'application/json', 10, :by) RETURNING id"
        ),
        {
            "external": f"retention-{when.timestamp()}",
            "hash": hashlib.sha256(str(when).encode()).digest(),
            "key": f"made-up/retention-{abs(hash(when))}",
            "by": LOADED_BY,
        },
    ).scalar_one()
    candidate_id = conn.execute(
        text(
            "INSERT INTO core.candidate (capture_id, source_key, created_by, created_at, "
            "pipeline_state) VALUES (:capture, :key, :by, :when, 'not_recorded') RETURNING id"
        ),
        {"capture": capture, "key": f"retention-{capture}", "by": LOADED_BY, "when": when},
    ).scalar_one()
    for number in range(fields):
        conn.execute(
            text(
                "INSERT INTO core.candidate_field (candidate_id, field, value, source, "
                "verification_status, recorded_by, recorded_at) VALUES (:c, :f, :v, "
                "'made-up test data', 'unverified', :by, :when)"
            ),
            {
                "c": candidate_id,
                "f": ["full_name", "phone", "email"][number % 3],
                "v": f"made up {number}",
                "by": LOADED_BY,
                "when": when,
            },
        )
    return candidate_id


def test_nothing_is_due_while_legal_has_not_answered(owner):
    assert in_force(owner) is None
    with pytest.raises(RetentionError, match="OPN-07"):
        due(owner)


def test_the_policy_in_force_is_the_latest_activation(owner, policy):
    assert in_force(owner) == policy
    rules = owner.execute(
        text(
            "SELECT applies_to, months FROM core.retention_rule WHERE policy_version = :v "
            "ORDER BY applies_to"
        ),
        {"v": policy},
    ).all()
    assert dict(rules) == {
        "hired": 24,
        "in_process": None,
        "no_application": 12,
        "rejected": 24,
        "withdrawn": 1,
    }


def test_a_policy_is_never_edited(owner, policy):
    body = json.loads(PROPOSED.read_text(encoding="utf-8"))
    body["version"] = policy
    with pytest.raises(RetentionError, match="already loaded"):
        load_policy(owner, body, LOADED_BY)


def test_a_candidate_who_never_applied_falls_due_and_is_erased(owner, policy):
    candidate_id = _candidate(owner, months_ago=18)
    rows = due(owner)
    mine = [row for row in rows if row["candidate_id"] == candidate_id]
    assert len(mine) == 1
    assert mine[0]["applies_to"] == "no_application"
    assert summarise(rows)["no_application"] >= 1

    done = erase_one(owner, candidate_id, by=LOADED_BY, policy=policy, due_at=mine[0]["due_at"])
    assert done["fields_erased"] == 3
    assert done["files_erased"] == 1
    assert done["blob_keys"] and all(key.startswith("made-up/") for key in done["blob_keys"])

    # The values are gone; the record, and the fact of the erasure, are not (BR-205).
    left = owner.execute(
        text("SELECT count(*) FROM core.candidate_field WHERE candidate_id = :c"),
        {"c": candidate_id},
    ).scalar_one()
    assert left == 0
    record = owner.execute(
        text("SELECT archived_at, archived_reason FROM core.candidate WHERE id = :c"),
        {"c": candidate_id},
    ).one()
    assert record.archived_at is not None
    assert record.archived_reason == "erased: retention"
    erasure = owner.execute(
        text(
            "SELECT reason, policy_version, fields_erased FROM core.erasure WHERE candidate_id = :c"
        ),
        {"c": candidate_id},
    ).one()
    assert (erasure.reason, erasure.policy_version, erasure.fields_erased) == (
        "retention",
        policy,
        3,
    )
    # The capture says its file is gone. It keeps the content hash, which names no one, so the
    # record still shows that a file was there and when it went (BR-107).
    capture = owner.execute(
        text(
            "SELECT k.erased_at, k.blob_key FROM raw.capture k "
            "JOIN core.candidate c ON c.capture_id = k.id WHERE c.id = :c"
        ),
        {"c": candidate_id},
    ).one()
    assert capture.erased_at is not None
    assert capture.blob_key
    assert not due(owner) or candidate_id not in {row["candidate_id"] for row in due(owner)}


def test_a_candidate_is_erased_once(owner, policy):
    candidate_id = _candidate(owner, months_ago=18)
    erase_one(owner, candidate_id, by=LOADED_BY, policy=policy)
    with pytest.raises(DBAPIError), owner.begin_nested():
        erase_one(owner, candidate_id, by=LOADED_BY, policy=policy)


def test_a_candidate_still_in_the_process_is_never_due(owner, policy, make_candidate):
    candidate_id = _candidate(owner, months_ago=60)
    owner.execute(
        text(
            "INSERT INTO pipeline.opening (brand, department, track, headcount, owner_recruiter, "
            "team, criteria_version_id, created_by) SELECT 'Made Up', 'Sales', 'A', 1, "
            "'dev|recruiter-a', 'team-a', id, :by FROM core.criteria_version LIMIT 1"
        ),
        {"by": LOADED_BY},
    )
    owner.execute(
        text(
            "INSERT INTO pipeline.application (opening_id, candidate_id, owner_recruiter, team, "
            "created_by) SELECT max(id), :c, 'dev|recruiter-a', 'team-a', :by FROM pipeline.opening"
        ),
        {"c": candidate_id, "by": LOADED_BY},
    )
    state = owner.execute(
        text("SELECT applies_to, due_at FROM core.candidate_retention WHERE candidate_id = :c"),
        {"c": candidate_id},
    ).one()
    assert state.applies_to == "in_process"
    assert state.due_at is None
    assert candidate_id not in {row["candidate_id"] for row in due(owner)}


def test_the_app_role_cannot_erase_anyone(app_engine, policy):
    with app_engine.connect() as conn:
        with pytest.raises(DBAPIError, match="permission denied"):
            conn.execute(
                text("SELECT * FROM core.erase_candidate(1, 'retention', NULL, 'someone', NULL)")
            )
        # Nor by hand, flag or no flag: there is no DELETE grant behind the trigger.
        with pytest.raises(DBAPIError):
            conn.execute(text("SELECT set_config('talent.erasing', 'on', true)"))
            conn.execute(text("DELETE FROM core.candidate_field WHERE candidate_id = 1"))
