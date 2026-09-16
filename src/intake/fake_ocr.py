"""A stand-in for the OCR API, for dev and tests only. It refuses to start anywhere else.

It returns saved, made-up sample answers (intake/fake_samples) and can be told to misbehave, so
every failure path is tested without the real API. What it does is set when it is built, or by a
marker inside the uploaded file, so a test can drive it through the public upload:

    FAKE-OCR:en | ar | mixed | hidden   which sample answer to return (default en)
    FAKE-OCR:slow                       wait slow_seconds first
    FAKE-OCR:fail                       the service is down (retried)
    FAKE-OCR:timeout                    no answer in time (retried)
    FAKE-OCR:reject                     the service will not read this file (not retried)
    FAKE-OCR:garbled                    an answer that is not in the expected shape

The samples are guesses at the real API's answer. Replace them with the OCR team's samples when
they arrive, so the fake returns exactly what the real API will.
"""

import re
import time
from collections.abc import Callable
from importlib import resources

from config import Environment
from intake.ocr import OcrRejected, OcrReply, OcrTimeout, OcrUnavailable

SAMPLES = ("en", "ar", "mixed", "hidden")
FAILURES = ("fail", "timeout", "reject", "garbled")
BEHAVIOURS = (*SAMPLES, *FAILURES, "slow")
_MARKER = re.compile(rb"FAKE-OCR:([a-z]+)")


def sample_answer(name: str) -> bytes:
    if name not in SAMPLES:
        raise ValueError(f"no fake sample named {name!r}")
    return resources.files("intake").joinpath("fake_samples", f"{name}.json").read_bytes()


class FakeOcrReader:
    name = "fake-ocr"

    def __init__(
        self,
        env: Environment,
        *,
        behaviour: str | None = None,
        slow_seconds: float = 2.0,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if env is not Environment.DEV:
            raise RuntimeError(
                f"The fake OCR runs only in dev and tests, not with TALENT_ENV={env}."
            )
        if behaviour is not None and behaviour not in BEHAVIOURS:
            raise ValueError(f"unknown fake OCR behaviour {behaviour!r}")
        self.behaviour = behaviour
        self.slow_seconds = slow_seconds
        self._sleep = sleep
        self.calls = 0

    def _asked(self, content: bytes) -> list[str]:
        found = [m.decode("ascii") for m in _MARKER.findall(content)]
        asked = [word for word in found if word in BEHAVIOURS]
        return [self.behaviour, *asked] if self.behaviour else asked

    def read(self, content: bytes, media_type: str) -> OcrReply:
        self.calls += 1
        asked = self._asked(content)
        if "slow" in asked:
            self._sleep(self.slow_seconds)
        failure = next((word for word in asked if word in FAILURES), None)
        if failure == "fail":
            raise OcrUnavailable("The fake OCR was told to fail.")
        if failure == "timeout":
            raise OcrTimeout("The fake OCR was told to time out.")
        if failure == "reject":
            raise OcrRejected("The fake OCR was told to refuse the file.")
        if failure == "garbled":
            return OcrReply(b'{"fields": "not an object"}', "application/json", self.name)
        sample = next((word for word in asked if word in SAMPLES), "en")
        return OcrReply(sample_answer(sample), "application/json", self.name)
