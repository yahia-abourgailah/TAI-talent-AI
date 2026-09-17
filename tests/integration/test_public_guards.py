"""What the public surface refuses before it reads anything (week 7 security check).

The upload and apply endpoints take requests from strangers. These prove the guards that do not
depend on what is in the body: size, and which browsers may call us at all.
"""

import pytest
from fastapi.testclient import TestClient

from api.app import create_app
from api.public_routes import MAX_PUBLIC_BODY
from config import Settings
from importer.blobs import MemoryBlobStore


def _settings(**extra) -> Settings:
    return Settings(
        _env_file=None,
        env="dev",
        auth_mode="dev",
        db_dsn="postgresql+psycopg://unused:unused@localhost:1/unused",
        redis_url="redis://localhost:1/0",
        blob_endpoint="http://localhost:1",
        blob_access_key="unused",
        blob_secret_key="unused",
        **extra,
    )


@pytest.fixture
def client(app_engine):
    from contextlib import contextmanager

    connection = app_engine.connect()

    @contextmanager
    def transaction():
        with connection.begin_nested():
            yield connection

    def build(**extra) -> TestClient:
        app = create_app(
            _settings(**extra), probes={}, transaction=transaction, blobs=MemoryBlobStore()
        )
        return TestClient(app)

    try:
        yield build
    finally:
        connection.rollback()
        connection.close()


def test_a_huge_application_is_refused_before_it_is_read(client):
    api = client()
    answer = api.post(
        "/v1/public/applications",
        content=b"x" * (MAX_PUBLIC_BODY + 1),
        headers={"Content-Type": "application/json", "Idempotency-Key": "a" * 36},
    )
    assert answer.status_code == 413
    assert answer.json()["error"]["code"] == "body_too_large"


def test_an_upload_without_a_length_is_refused(client):
    api = client()
    answer = api.post(
        "/v1/public/cv-uploads",
        content=iter([b"%PDF-1.4 made up"]),  # chunked: no Content-Length
        headers={"Content-Type": "application/octet-stream"},
    )
    assert answer.status_code == 411
    assert answer.json()["error"]["code"] == "length_required"


def test_no_browser_on_another_origin_may_call_us_by_default(client):
    api = client()
    answer = api.get("/v1/public/requisitions", headers={"Origin": "https://careers.example.com"})
    assert answer.status_code == 200
    assert "access-control-allow-origin" not in {k.lower() for k in answer.headers}


def test_the_pages_we_name_may_call_us(client):
    api = client(cors_origins="https://careers.example.com, https://crm.example.com")
    allowed = api.get("/v1/public/requisitions", headers={"Origin": "https://careers.example.com"})
    assert allowed.headers["access-control-allow-origin"] == "https://careers.example.com"
    # Cookies are never part of this, so a stolen session cannot be replayed from a page.
    assert "access-control-allow-credentials" not in {k.lower() for k in allowed.headers}

    other = api.get("/v1/public/requisitions", headers={"Origin": "https://not-us.example.com"})
    assert "access-control-allow-origin" not in {k.lower() for k in other.headers}

    preflight = api.options(
        "/v1/public/applications",
        headers={
            "Origin": "https://careers.example.com",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type, idempotency-key, x-upload-token",
        },
    )
    assert preflight.status_code == 200
    assert preflight.headers["access-control-allow-origin"] == "https://careers.example.com"
