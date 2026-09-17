"""The development console is a development tool, and only that (week 8).

It is a plain client of the /v1 API, served by the platform so the whole flow can be tried by hand.
The dashboard belongs to the CRM team and the careers page to the website team; neither of these
pages has been through their review, so neither is served anywhere but a developer's machine.
"""

from contextlib import contextmanager

import pytest
from fastapi.testclient import TestClient

from api.app import create_app
from config import Settings
from importer.blobs import MemoryBlobStore


def _client(app_engine, **extra) -> TestClient:
    connection = app_engine.connect()

    @contextmanager
    def transaction():
        with connection.begin_nested():
            yield connection

    settings = Settings(
        _env_file=None,
        db_dsn="postgresql+psycopg://unused:unused@localhost:1/unused",
        redis_url="redis://localhost:1/0",
        blob_endpoint="http://localhost:1",
        blob_access_key="unused-but-long-enough-for-a-key",
        blob_secret_key="unused-but-long-enough-for-a-key",
        **extra,
    )
    return TestClient(
        create_app(settings, probes={}, transaction=transaction, blobs=MemoryBlobStore())
    )


def test_the_console_is_served_in_development(app_engine):
    client = _client(app_engine, env="dev", auth_mode="dev")
    console = client.get("/app/")
    assert console.status_code == 200
    assert "Talent Platform" in console.text
    assert client.get("/app/app.js").status_code == 200
    careers = client.get("/careers/")
    assert careers.status_code == 200
    assert "Careers" in careers.text


@pytest.mark.parametrize("env", ["staging", "prod"])
def test_it_is_served_nowhere_else(app_engine, env):
    client = _client(
        app_engine,
        env=env,
        auth_mode="oidc",
        oidc_issuer="https://login.example.com",
        oidc_audience="talent-platform",
        ocr_mode="api",
        ocr_base_url="https://ocr.internal",
    )
    for path in ("/app/", "/app/app.js", "/careers/"):
        assert client.get(path).status_code == 404, path
    # The API itself is unchanged by any of this.
    assert client.get("/health").status_code == 200
