"""BR-201, BR-205, BR-303: nothing is deleted, and recorded history is never overwritten.

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
            "INSERT INTO core.candidate (capture_id, source_key, created_by, pipeline_state) "
            "VALUES (:c, :k, 'integration-test', 'not_recorded') RETURNING id"
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
    return {
        "candidate": candidate_id,
        "capture": capture_id,
        "field": field_id,
        "evaluation": evaluation_id,
    }


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


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE core.candidate_field SET value = '31' WHERE id = :id",
        "UPDATE core.candidate_field SET verification_status = 'verified', "
        "verified_at = now(), verified_by = 'someone' WHERE id = :id",
        "UPDATE core.candidate_field SET source = 'changed' WHERE id = :id",
    ],
)
def test_the_app_cannot_overwrite_a_field(app_engine, statement):
    with app_engine.connect() as conn:
        ids = _seed(conn)
        with pytest.raises(DBAPIError) as error:
            conn.execute(text(statement), {"id": ids["field"]})
        assert _sqlstate(error) == "42501"


def test_even_the_owner_cannot_overwrite_a_field(owner_engine):
    with owner_engine.connect() as conn:
        ids = _seed(conn)
        with pytest.raises(DBAPIError) as error:
            conn.execute(
                text("UPDATE core.candidate_field SET value = '31' WHERE id = :id"),
                {"id": ids["field"]},
            )
        assert "immutable" in str(error.value.orig)


def test_a_correction_is_a_new_row_and_the_current_view_shows_it(app_engine):
    with app_engine.connect() as conn:
        ids = _seed(conn)
        conn.execute(
            text(
                "INSERT INTO core.candidate_field "
                "(candidate_id, field, value, source, verification_status, verified_at, "
                " verified_by, recorded_by) "
                "VALUES (:c, 'age', '31', 'checked with the candidate', 'verified', now(), "
                " 'reviewer-a', 'reviewer-a')"
            ),
            {"c": ids["candidate"]},
        )
        history = conn.execute(
            text(
                "SELECT count(*) FROM core.candidate_field "
                "WHERE candidate_id = :c AND field = 'age'"
            ),
            {"c": ids["candidate"]},
        ).scalar_one()
        current = conn.execute(
            text(
                "SELECT value, verification_status FROM core.candidate_field_current "
                "WHERE candidate_id = :c AND field = 'age'"
            ),
            {"c": ids["candidate"]},
        ).one()
    assert history == 2
    assert tuple(current) == ("31", "verified")


def test_the_import_keeps_one_row_per_field(app_engine):
    with app_engine.connect() as conn:
        ids = _seed(conn)
        statement = text(
            "INSERT INTO core.candidate_field (candidate_id, field, value, source) "
            "VALUES (:c, 'title', 'Sales Rep', 'migrated from TAI_Master')"
        )
        conn.execute(statement, {"c": ids["candidate"]})
        with pytest.raises(DBAPIError) as error:
            conn.execute(statement, {"c": ids["candidate"]})
        assert _sqlstate(error) == "23505"


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


@pytest.mark.parametrize(
    ("status", "verified_at", "verified_by"),
    [("verified", "NULL", "NULL"), ("unverified", "now()", "NULL")],
)
def test_verification_details_belong_only_to_a_verified_field(
    app_engine, status, verified_at, verified_by
):
    with app_engine.connect() as conn:
        ids = _seed(conn)
        with pytest.raises(DBAPIError) as error:
            conn.execute(
                text(
                    "INSERT INTO core.candidate_field "
                    "(candidate_id, field, value, source, verification_status, verified_at, "
                    " verified_by) "
                    f"VALUES (:c, 'email', 'a@example.com', 'workbook', :status, {verified_at}, "
                    f" {verified_by})"
                ),
                {"c": ids["candidate"], "status": status},
            )
        assert _sqlstate(error) == "23514"


def test_the_app_may_change_only_a_candidates_archive_columns(app_engine):
    with app_engine.connect() as conn:
        ids = _seed(conn)
        with pytest.raises(DBAPIError) as error:
            conn.execute(
                text("UPDATE core.candidate SET source_key = 'someone-else' WHERE id = :id"),
                {"id": ids["candidate"]},
            )
        assert _sqlstate(error) == "42501"


def test_even_the_owner_cannot_change_what_a_candidate_came_from(owner_engine):
    with owner_engine.connect() as conn:
        ids = _seed(conn)
        with pytest.raises(DBAPIError) as error:
            conn.execute(
                text("UPDATE core.candidate SET pipeline_state = 'recorded_in_raw' WHERE id = :id"),
                {"id": ids["candidate"]},
            )
        assert "never changes" in str(error.value.orig)


def test_a_candidate_must_say_whether_its_pipeline_was_recorded(app_engine):
    with app_engine.connect() as conn:
        ids = _seed(conn)
        with pytest.raises(DBAPIError) as error:
            conn.execute(
                text(
                    "INSERT INTO core.candidate (capture_id, source_key, created_by) "
                    "VALUES (:c, :k, 'integration-test')"
                ),
                {"c": ids["capture"], "k": uuid.uuid4().hex},
            )
        assert _sqlstate(error) == "23502"


def test_computed_evaluations_may_repeat_but_a_stored_one_may_not(app_engine):
    with app_engine.connect() as conn:
        ids = _seed(conn)
        for model in ("model-a", "model-b"):
            conn.execute(
                text(
                    "INSERT INTO core.evaluation "
                    "(candidate_id, criteria_version_id, origin, score, model_version, "
                    " recorded_by) "
                    "VALUES (:c, :v, 'computed', 70, :m, 'integration-test')"
                ),
                {"c": ids["candidate"], "v": VERSION, "m": model},
            )
        with pytest.raises(DBAPIError) as error:
            conn.execute(
                text(
                    "INSERT INTO core.evaluation "
                    "(candidate_id, criteria_version_id, origin, score, recorded_by) "
                    "VALUES (:c, :v, 'stored', 71, 'integration-test')"
                ),
                {"c": ids["candidate"], "v": VERSION},
            )
        assert _sqlstate(error) == "23505"


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
                    "INSERT INTO core.candidate "
                    "(capture_id, source_key, created_by, pipeline_state) "
                    "VALUES (:c, :k, 'integration-test', 'not_recorded')"
                ),
                {"c": capture_id, "k": source_key},
            )
        assert _sqlstate(error) == "23505"
