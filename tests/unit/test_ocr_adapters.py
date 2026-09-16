"""The OCR adapter: the fake, the HTTP client, and the setting that picks one (NFR-01, CR-01)."""

import httpx
import pytest

from config import Environment
from intake.answer import parse_answer
from intake.fake_ocr import FakeOcrReader
from intake.ocr import (
    OcrLimits,
    OcrRejected,
    OcrTimeout,
    OcrUnavailable,
    reader_from_settings,
)
from intake.ocr_http import HttpOcrReader

CV = b"%PDF-1.4 made-up test CV"


@pytest.mark.parametrize("env", [Environment.STAGING, Environment.PROD])
def test_the_fake_refuses_to_start_outside_dev(env):
    with pytest.raises(RuntimeError, match="only in dev"):
        FakeOcrReader(env)


def test_the_fake_returns_samples_and_misbehaves_when_told():
    slept: list[float] = []
    fake = FakeOcrReader(Environment.DEV, sleep=slept.append, slow_seconds=3)
    assert parse_answer(fake.read(CV, "application/pdf").body).document.language == "en"
    arabic = fake.read(CV + b" FAKE-OCR:ar FAKE-OCR:slow", "application/pdf")
    assert parse_answer(arabic.body).document.language == "ar"
    assert slept == [3]
    hidden = fake.read(CV + b" FAKE-OCR:hidden", "application/pdf")
    assert parse_answer(hidden.body).hidden_content.found is True
    with pytest.raises(OcrUnavailable):
        fake.read(CV + b" FAKE-OCR:fail", "application/pdf")
    with pytest.raises(OcrTimeout):
        fake.read(CV + b" FAKE-OCR:timeout", "application/pdf")
    with pytest.raises(OcrRejected):
        fake.read(CV + b" FAKE-OCR:reject", "application/pdf")
    assert FakeOcrReader(Environment.DEV, behaviour="garbled").read(CV, "x").body.startswith(b"{")
    assert fake.calls == 6


def _reader(handler) -> HttpOcrReader:
    return HttpOcrReader(
        "http://ocr.internal:8100/v1/", api_key="k", transport=httpx.MockTransport(handler)
    )


def test_the_http_reader_posts_the_file_and_returns_the_answer_untouched():
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["auth"] = request.headers.get("authorization")
        seen["body_has_file"] = CV in request.read()
        return httpx.Response(200, content=b'{"fields": {}}', headers={"content-type": "x/y"})

    reply = _reader(handler).read(CV, "application/pdf")
    assert (reply.body, reply.media_type, reply.reader) == (b'{"fields": {}}', "x/y", "ocr-api")
    assert seen == {
        "url": "http://ocr.internal:8100/v1/ocr",
        "auth": "Bearer k",
        "body_has_file": True,
    }


@pytest.mark.parametrize(
    ("status", "error"),
    [
        (429, OcrUnavailable),
        (408, OcrUnavailable),
        (500, OcrUnavailable),
        (503, OcrUnavailable),
        (400, OcrRejected),
        (415, OcrRejected),
        (401, OcrRejected),
    ],
)
def test_the_http_reader_sorts_failures_into_retry_or_not(status, error):
    reader = _reader(lambda request: httpx.Response(status, content=b"Fake Person Name"))
    with pytest.raises(error) as caught:
        reader.read(CV, "application/pdf")
    assert "Fake Person" not in str(caught.value)


def test_the_http_reader_reports_timeouts_and_lost_connections():
    def slow(request):
        raise httpx.ReadTimeout("slow", request=request)

    def gone(request):
        raise httpx.ConnectError("gone", request=request)

    with pytest.raises(OcrTimeout):
        _reader(slow).read(CV, "application/pdf")
    with pytest.raises(OcrUnavailable):
        _reader(gone).read(CV, "application/pdf")


def test_the_setting_picks_the_reader(make_settings):
    assert isinstance(reader_from_settings(make_settings()), FakeOcrReader)
    api = make_settings(ocr_mode="api", ocr_base_url="http://ocr.internal:8100/v1")
    assert isinstance(reader_from_settings(api), HttpOcrReader)
    with pytest.raises(ValueError, match="TALENT_OCR_BASE_URL"):
        reader_from_settings(make_settings(ocr_mode="api"))


def test_the_fake_cannot_be_chosen_outside_dev(make_settings):
    with pytest.raises(ValueError, match="fake OCR only runs in dev"):
        make_settings(env="staging", ocr_mode="fake")
    staging = make_settings(env="staging", ocr_base_url="https://ocr.internal/v1")
    assert staging.ocr_mode_in_force.value == "api"
    with pytest.raises(ValueError, match="http or https"):
        make_settings(ocr_base_url="ftp://ocr.internal")


def test_retry_gaps_grow_and_then_repeat():
    limits = OcrLimits(retry_gaps_seconds=(30, 120, 600))
    assert [limits.gap_before(n) for n in (2, 3, 4, 5, 9)] == [30, 120, 600, 600, 600]
