"""BR-107: original captures are written once and never changed - enforced by the database.

Every test runs inside a transaction that is rolled back, so nothing is left behind.
"""

import hashlib
import re
import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

INSERT = text(
    """
    INSERT INTO raw.capture
      (source, external_id, content_sha256, blob_key, media_type, byte_size, received_by)
    VALUES
      (:source, :external_id, :sha, :blob_key, 'application/pdf', 1024, 'integration-test')
    RETURNING id
    """
)


def _capture() -> dict[str, object]:
    return {
        "source": "integration-test",
        "external_id": uuid.uuid4().hex,
        "sha": hashlib.sha256(uuid.uuid4().bytes).digest(),
        "blob_key": f"test/{uuid.uuid4().hex}",
    }


def _sqlstate(error: pytest.ExceptionInfo[DBAPIError]) -> str:
    return error.value.orig.sqlstate  # type: ignore[union-attr]


def test_app_role_can_append_and_read(app_engine):
    with app_engine.connect() as conn:
        capture_id = conn.execute(INSERT, _capture()).scalar_one()
        found = conn.execute(text("SELECT id FROM raw.capture WHERE id = :id"), {"id": capture_id})
        assert found.scalar_one() == capture_id


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE raw.capture SET blob_key = 'tampered' WHERE id = :id",
        "DELETE FROM raw.capture WHERE id = :id",
    ],
)
def test_app_role_has_no_grant_to_change(app_engine, statement):
    with app_engine.connect() as conn:
        capture_id = conn.execute(INSERT, _capture()).scalar_one()
        with pytest.raises(DBAPIError) as error:
            conn.execute(text(statement), {"id": capture_id})
        assert _sqlstate(error) == "42501"
        assert "permission denied" in str(error.value.orig)


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE raw.capture SET blob_key = 'tampered' WHERE id = :id",
        "DELETE FROM raw.capture WHERE id = :id",
        # CASCADE gets past the foreign keys from core, so the refusal can only come from a trigger.
        "TRUNCATE raw.capture CASCADE",
    ],
)
def test_even_the_owner_cannot_change(owner_engine, statement):
    with owner_engine.connect() as conn:
        capture_id = conn.execute(INSERT, _capture()).scalar_one()
        with pytest.raises(DBAPIError) as error:
            conn.execute(text(statement), {"id": capture_id})
        # A cascaded truncate may reach core's immutable tables before raw.capture's trigger.
        assert re.search("append-only|immutable", str(error.value.orig))


def test_same_capture_twice_is_refused(app_engine):
    capture = _capture()
    with app_engine.connect() as conn:
        conn.execute(INSERT, capture)
        with pytest.raises(DBAPIError) as error:
            conn.execute(INSERT, capture)
        assert _sqlstate(error) == "23505"


def test_app_role_cannot_create_tables(app_engine):
    with app_engine.connect() as conn, pytest.raises(DBAPIError) as error:
        conn.execute(text("CREATE TABLE raw.sneaky (id int)"))
    assert _sqlstate(error) == "42501"
