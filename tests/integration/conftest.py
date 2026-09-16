import json
import os
from contextlib import contextmanager
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

from api.app import create_app
from config import Settings
from importer.blobs import MemoryBlobStore
from pipeline.dev_candidate import create_demo_candidate
from pipeline.lists import load_step_list

PROPOSED_LIST = (
    Path(__file__).resolve().parents[2] / "docs" / "pipeline" / "lists" / "proposed-2026-09-15.json"
)
PROVISIONAL_LIST = "provisional-brd-2026-09"


def _engine(variable: str) -> Engine:
    dsn = os.environ.get(variable)
    if not dsn:
        pytest.skip(f"{variable} is not set; integration tests need a migrated database")
    return create_engine(dsn)


@pytest.fixture(scope="session")
def owner_engine() -> Engine:
    return _engine("TALENT_TEST_OWNER_DSN")


@pytest.fixture(scope="session")
def app_engine() -> Engine:
    return _engine("TALENT_TEST_APP_DSN")


@pytest.fixture
def api_client(app_engine):
    """The app, signed in with dev accounts, on one connection that is rolled back at the end."""
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
    app = create_app(settings, probes={}, transaction=transaction, blobs=MemoryBlobStore())
    client = TestClient(app)
    try:
        yield client, connection
    finally:
        connection.rollback()
        connection.close()


def sign_in(client: TestClient, account: str) -> dict[str, str]:
    token = client.post("/dev/token", json={"account": account}).json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def make_candidate():
    """A made-up candidate with the given fields, e.g. make(conn, age="25")."""

    def make(conn, **fields: object) -> int:
        candidate_id = create_demo_candidate(conn, "integration-test", source="test-week4")
        for field, value in fields.items():
            conn.execute(
                text(
                    "INSERT INTO core.candidate_field "
                    "(candidate_id, field, value, source, verification_status, recorded_by) "
                    "VALUES (:c, :f, :v, 'made-up test data', :s, 'integration-test')"
                ),
                {
                    "c": candidate_id,
                    "f": field,
                    "v": None if value is None else str(value),
                    "s": "not_recorded" if value is None else "unverified",
                },
            )
        return candidate_id

    return make


def _activate(conn, version: str) -> None:
    conn.execute(
        text(
            "INSERT INTO pipeline.list_activation (list_version, activated_by) "
            "VALUES (:v, 'integration-test')"
        ),
        {"v": version},
    )


@pytest.fixture
def use_proposed_list():
    """Puts the proposed reason list in force inside the test's transaction."""

    def use(conn) -> None:
        document = json.loads(PROPOSED_LIST.read_text(encoding="utf-8"))
        exists = conn.execute(
            text("SELECT 1 FROM pipeline.step_list WHERE version = :v"), {"v": document["version"]}
        ).first()
        if exists:
            _activate(conn, document["version"])
        else:
            load_step_list(conn, document, "integration-test")

    return use


@pytest.fixture
def use_provisional_list():
    def use(conn) -> None:
        _activate(conn, PROVISIONAL_LIST)

    return use
