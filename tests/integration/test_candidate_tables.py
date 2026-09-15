"""BR-205: the app never deletes. Criteria versions and evaluations are never changed.

Every test runs inside a transaction that is rolled back, so nothing is left behind.
Made-up data only.
"""

import hashlib
import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

VERSION = "test-version"


def _sqlstate(error: pytest.ExceptionInfo[DBAPIError]) -> str:
    return error.value.orig.sqlstate  # type: ignore[union-attr]


def _seed(conn) -> dict[str, int]:
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
    conn.execute(
        text(
            "INSERT INTO core.criteria_version "
            "(id, ruleset_module, description, effective_from, created_by) "
            "VALUES (:v, 'test', 'test', '2026-01-01', 'integration-test') ON CONFLICT DO NOTHING"
        ),
        {"v": VERSION},
    )
    candidate_id = conn.execute(
        text(
            "INSERT INTO core.candidate (capture_id, source_key, created_by) "
            "VALUES (:c, :k, 'integration-test') RETURNING id"
        ),
        {"c": capture_id, "k": uuid.uuid4().hex},
    ).scalar_one()
    field_id = conn.execute(
        text(
            "INSERT INTO core.candidate_field (candidate_id, field, value, source) "
            "VALUES (:c, 'age', '30', 'workbook:stated') RETURNING id"
        ),
        {"c": candidate_id},
    ).scalar_one()
    evaluation_id = conn.execute(
        text(
            "INSERT INTO core.evaluation "
            "(candidate_id, criteria_version_id, origin, score, tier, recorded_by) "
            "VALUES (:c, :v, 'stored', 72.5, 'B', 'integration-test') RETURNING id"
        ),
        {"c": candidate_id, "v": VERSION},
    ).scalar_one()
    return {"candidate": candidate_id, "field": field_id, "evaluation": evaluation_id}


@pytest.mark.parametrize(
    ("table", "key"),
    [
        ("core.candidate", "candidate"),
        ("core.candidate_field", "field"),
        ("core.evaluation", "evaluation"),
    ],
)
def test_app_role_cannot_delete(app_engine, table, key):
    with app_engine.connect() as conn:
        ids = _seed(conn)
        with pytest.raises(DBAPIError) as error:
            conn.execute(text(f"DELETE FROM {table} WHERE id = :id"), {"id": ids[key]})
        assert _sqlstate(error) == "42501"


def test_app_role_cannot_delete_criteria_version(app_engine):
    with app_engine.connect() as conn:
        _seed(conn)
        with pytest.raises(DBAPIError) as error:
            conn.execute(text("DELETE FROM core.criteria_version WHERE id = :v"), {"v": VERSION})
        assert _sqlstate(error) == "42501"


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE core.evaluation SET score = 99 WHERE id = :id",
        "UPDATE core.criteria_version SET description = 'changed' WHERE id = 'test-version'",
    ],
)
def test_app_role_cannot_change_verdicts_or_versions(app_engine, statement):
    with app_engine.connect() as conn:
        ids = _seed(conn)
        with pytest.raises(DBAPIError) as error:
            conn.execute(text(statement), {"id": ids["evaluation"]})
        assert _sqlstate(error) == "42501"


def test_even_the_owner_cannot_change_an_evaluation(owner_engine):
    with owner_engine.connect() as conn:
        ids = _seed(conn)
        with pytest.raises(DBAPIError) as error:
            conn.execute(
                text("UPDATE core.evaluation SET score = 99 WHERE id = :id"),
                {"id": ids["evaluation"]},
            )
        assert "immutable" in str(error.value.orig)


def test_not_recorded_cannot_hold_a_value(app_engine):
    with app_engine.connect() as conn:
        ids = _seed(conn)
        with pytest.raises(DBAPIError) as error:
            conn.execute(
                text(
                    "INSERT INTO core.candidate_field "
                    "(candidate_id, field, value, source, verification_status) "
                    "VALUES (:c, 'phone', 'guessed', 'workbook', 'not_recorded')"
                ),
                {"c": ids["candidate"]},
            )
        assert _sqlstate(error) == "23514"


def test_verified_needs_who_and_when(app_engine):
    with app_engine.connect() as conn:
        ids = _seed(conn)
        with pytest.raises(DBAPIError) as error:
            conn.execute(
                text(
                    "INSERT INTO core.candidate_field "
                    "(candidate_id, field, value, source, verification_status) "
                    "VALUES (:c, 'email', 'a@example.com', 'workbook', 'verified')"
                ),
                {"c": ids["candidate"]},
            )
        assert _sqlstate(error) == "23514"


def test_same_source_row_is_one_candidate(app_engine):
    with app_engine.connect() as conn:
        ids = _seed(conn)
        capture_id, source_key = conn.execute(
            text("SELECT capture_id, source_key FROM core.candidate WHERE id = :id"),
            {"id": ids["candidate"]},
        ).one()
        with pytest.raises(DBAPIError) as error:
            conn.execute(
                text(
                    "INSERT INTO core.candidate (capture_id, source_key, created_by) "
                    "VALUES (:c, :k, 'integration-test')"
                ),
                {"c": capture_id, "k": source_key},
            )
        assert _sqlstate(error) == "23505"
