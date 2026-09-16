"""The one door to the OCR (NFR-01, CR-01). Everything that reads a CV goes through an OcrReader.

Two versions exist, and TALENT_OCR_MODE picks one:

    api   HttpOcrReader: the company OCR API on our own host (intake.ocr_http)
    fake  FakeOcrReader: saved sample answers, dev and tests only (intake.fake_ocr)

A reader returns the OCR's answer exactly as it came, as bytes. Turning it into fields is
intake.answer and intake.cv_fields, so the answer can be saved before anything reads it.

A failure is one of three kinds, because they are handled differently:

    OcrTimeout      no answer in time           retried with growing gaps
    OcrUnavailable  the service is down or busy retried with growing gaps
    OcrRejected     the service will not read   not retried: a person looks at the CV
                    this file

No error message carries CV content: messages name the kind of failure and a status code only.
"""

from dataclasses import dataclass
from typing import Protocol

from config import OcrMode, Settings


class OcrError(Exception):
    """Reading failed. `code` is stable and is what gets recorded."""

    code = "ocr_failed"
    retryable = False


class OcrTimeout(OcrError):
    code = "ocr_timed_out"
    retryable = True


class OcrUnavailable(OcrError):
    code = "ocr_unavailable"
    retryable = True


class OcrRejected(OcrError):
    code = "ocr_rejected"
    retryable = False


@dataclass(frozen=True, slots=True)
class OcrReply:
    """The OCR's answer as it came, and which reader produced it."""

    body: bytes
    media_type: str
    reader: str


class OcrReader(Protocol):
    name: str

    def read(self, content: bytes, media_type: str) -> OcrReply:
        """Reads one CV. Raises an OcrError when it cannot."""
        ...


@dataclass(frozen=True, slots=True)
class OcrLimits:
    """How hard the OCR may be pushed (NFR-01). Safe defaults until the OCR team gives numbers."""

    max_concurrency: int = 2
    timeout_seconds: float = 60.0
    max_attempts: int = 4
    # Gap before attempt 2, 3, 4...; the last gap repeats.
    retry_gaps_seconds: tuple[int, ...] = (30, 120, 600)
    # When every reading slot is busy, the job waits this long and looks again.
    busy_wait_seconds: int = 5

    def gap_before(self, attempt: int) -> int:
        """Seconds to wait before `attempt` (2 or more)."""
        index = min(max(attempt - 2, 0), len(self.retry_gaps_seconds) - 1)
        return self.retry_gaps_seconds[index]

    @classmethod
    def from_settings(cls, settings: Settings) -> "OcrLimits":
        return cls(
            max_concurrency=settings.ocr_max_concurrency,
            timeout_seconds=settings.ocr_timeout_seconds,
            max_attempts=settings.ocr_max_attempts,
        )


def reader_from_settings(settings: Settings) -> OcrReader:
    """The reader TALENT_OCR_MODE asks for. Refuses a misconfiguration before any CV is read."""
    if settings.ocr_mode_in_force is OcrMode.FAKE:
        from intake.fake_ocr import FakeOcrReader

        return FakeOcrReader(settings.env)

    from intake.ocr_http import HttpOcrReader

    if not settings.ocr_base_url:
        raise ValueError(
            "TALENT_OCR_MODE is api but TALENT_OCR_BASE_URL is not set. Set it to the OCR API on "
            "our own host, or set TALENT_OCR_MODE=fake on a dev machine."
        )
    return HttpOcrReader(
        settings.ocr_base_url,
        api_key=settings.ocr_api_key.get_secret_value(),
        timeout_seconds=settings.ocr_timeout_seconds,
    )
