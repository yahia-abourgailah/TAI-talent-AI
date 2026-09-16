"""The public upload, without a database: file types from content, size and rate limits (BR-102)."""

import io
import zipfile
from contextlib import contextmanager

import pytest
from fastapi.testclient import TestClient

from api.app import create_app
from api.limits import UPLOADS, RateLimiter
from importer.blobs import MemoryBlobStore
from intake import files


def docx_bytes(text: str = "made-up") -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
        archive.writestr("word/document.xml", f"<document>{text}</document>")
    return buffer.getvalue()


def zip_bytes() -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("notes.txt", "made-up")
    return buffer.getvalue()


@pytest.mark.parametrize(
    ("content", "media_type"),
    [
        (b"%PDF-1.7\n...", files.PDF),
        (b"\x89PNG\r\n\x1a\n....", files.PNG),
        (b"\xff\xd8\xff\xe0....", files.JPEG),
        (docx_bytes(), files.DOCX),
    ],
)
def test_the_type_comes_from_the_content(content, media_type):
    kind = files.sniff(content)
    assert kind is not None and kind.media_type == media_type


@pytest.mark.parametrize(
    "content",
    [b"MZ\x90\x00 an exe named cv.pdf", b"<html>", zip_bytes(), b"PK\x03\x04 broken", b"GIF89a"],
)
def test_anything_else_is_refused(content):
    assert files.sniff(content) is None


@contextmanager
def _no_database():
    yield None


@pytest.fixture
def client(make_settings):
    settings = make_settings(auth_mode="dev", oidc_issuer="", oidc_audience="")
    app = create_app(
        settings,
        probes={},
        transaction=_no_database,
        blobs=MemoryBlobStore(),
        rate_limiter=RateLimiter(),
    )
    return TestClient(app, raise_server_exceptions=False)


def test_a_file_of_the_wrong_type_is_refused_by_its_content(client):
    response = client.post(
        "/v1/public/cv-uploads",
        files={"file": ("Fake Person CV.pdf", b"MZ not a pdf", "application/pdf")},
    )
    assert response.status_code == 415
    error = response.json()["error"]
    assert error["code"] == "unsupported_file_type"
    assert "Fake Person" not in response.text


def test_an_empty_file_is_refused(client):
    response = client.post("/v1/public/cv-uploads", files={"file": ("cv.pdf", b"", "x/y")})
    assert response.status_code == 400


def test_a_body_larger_than_the_limit_is_refused_before_it_is_read(client):
    response = client.post(
        "/v1/public/cv-uploads",
        content=b"x",
        headers={"Content-Length": str(files.MAX_BYTES * 2), "Content-Type": "multipart/form-data"},
    )
    assert response.status_code == 413
    assert response.json()["error"]["code"] == "file_too_large"
    assert response.headers["x-request-id"]


def test_a_file_just_over_the_limit_is_refused(client):
    big = b"%PDF-" + b"0" * files.MAX_BYTES
    response = client.post("/v1/public/cv-uploads", files={"file": ("cv.pdf", big, "x/y")})
    assert response.status_code == 413


def test_uploads_are_rate_limited_per_client(client):
    for _ in range(UPLOADS.requests):
        response = client.post("/v1/public/cv-uploads", files={"file": ("a", b"nope", "x/y")})
        assert response.status_code == 415
    limited = client.post("/v1/public/cv-uploads", files={"file": ("a", b"nope", "x/y")})
    assert limited.status_code == 429
    assert limited.json()["error"]["code"] == "rate_limited"
    assert int(limited.headers["retry-after"]) > 0


def test_the_status_needs_the_upload_token(client):
    response = client.get("/v1/public/cv-uploads/upl_1")
    assert response.status_code == 400
    malformed = client.get("/v1/public/cv-uploads/cand_1", headers={"X-Upload-Token": "t"})
    assert malformed.status_code == 404


def test_the_limiter_counts_per_window_and_client():
    now = [0.0]
    limiter = RateLimiter(clock=lambda: now[0])
    rule = UPLOADS
    for _ in range(rule.requests):
        assert limiter.check(rule, "10.0.0.1") is None
    assert limiter.check(rule, "10.0.0.1") == rule.window_seconds
    assert limiter.check(rule, "10.0.0.2") is None
    now[0] = rule.window_seconds + 1
    assert limiter.check(rule, "10.0.0.1") is None
