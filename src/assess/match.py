"""Matching one CV against what a job asks for, by the company CV service (BR-305, CR-01).

The real contract, read from the service itself on 20 September 2026:

    POST {TALENT_OCR_BASE_URL}/extract_and_match
    Content-Type: multipart/form-data
      file          the CV, named with the extension of the type we sniffed
      requirements  JSON: {"job_title": ..., "skill_types": [{"name", "levels", "skills"}]}
    X-API-Key: {TALENT_OCR_API_KEY}

    200  {"cv": ..., "match_report": {"overall_match_percentage", "matched", "below", "missing"},
          "metadata": ...}
    403  our key: a person fixes the configuration, not the CV
    408, 429, 5xx  busy or down: tried again later
    other 4xx      the file cannot be read: a person looks at it

The service extracts the CV with a model and then matches in code: same CV and same requirements,
same percentage. Nothing here decides anything — the percentage and the lines behind it go to a
person (CR-05).
"""

import json
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

import httpx

from intake.files import EXTENSION
from intake.ocr import OcrRejected, OcrTimeout, OcrUnavailable

PATH = "/extract_and_match"
API_KEY_HEADER = "X-API-Key"
FILE_PART = "file"
REQUIREMENTS_PART = "requirements"
RETRYABLE_STATUS = frozenset({408, 425, 429})
# The four the service grades against. A requisition may only ask for one of these (migration
# 0020), because a level it does not know is a percentage that means nothing.
LEVELS = ("beginner", "intermediate", "advanced", "expert")
DEFAULT_CATEGORY = "Technical"
# What produced the numbers, kept on every evaluation so an old one can be read with the rules it
# was made under. It changes when what we send or how we read the answer changes.
MATCHER_VERSION = "extract_and_match/2026-09-20"


class SkillMatcher(Protocol):
    """What the worker needs: one call that grades a CV, and a client to close afterwards."""

    def match(self, content: bytes, media_type: str, requirements: str) -> "MatchReport": ...

    def close(self) -> None: ...


class MatchUnreadable(Exception):
    """The service answered, but not with a match report we can record."""


@dataclass(frozen=True, slots=True)
class Line:
    """One skill the job asks for, and what the CV showed of it."""

    skill: str
    category: str
    wanted: str
    found: str | None
    evidence: str | None
    status: str

    def as_text(self) -> str:
        if self.status == "matched":
            shown = f"{self.skill}: {self.found}, {self.wanted} asked for"
        elif self.status == "below":
            shown = f"{self.skill}: {self.found}, below the {self.wanted} asked for"
        else:
            shown = f"{self.skill}: not in the CV, {self.wanted} asked for"
        return f"{shown} — “{self.evidence}”" if self.evidence else shown


@dataclass(frozen=True, slots=True)
class MatchReport:
    percentage: float
    lines: tuple[Line, ...]
    job_title: str
    # What the service says about the run: how it read the file, and its own hash of the
    # requirements it graded against. Both are kept on the evaluation, so a reading years from now
    # still names what produced it and what it was asked.
    method: str = ""
    requirements_hash: str = ""

    @property
    def met(self) -> tuple[Line, ...]:
        return tuple(line for line in self.lines if line.status == "matched")

    @property
    def unmet(self) -> tuple[Line, ...]:
        return tuple(line for line in self.lines if line.status != "matched")


def requirements_payload(job_title: str, wanted: Iterable[Mapping[str, Any]]) -> str:
    """What the opening asks for, in the shape the service takes.

    Skills are grouped by category, because that is how the service reports them back; a skill
    with no category of its own is Technical, which is what the service's own examples use.
    """
    groups: dict[str, list[dict[str, str]]] = {}
    for item in wanted:
        category = str(item.get("category") or DEFAULT_CATEGORY).strip() or DEFAULT_CATEGORY
        groups.setdefault(category, []).append(
            {"name": str(item["skill"]).strip(), "required_level": str(item["level"]).strip()}
        )
    if not groups:
        raise MatchUnreadable("The job asks for nothing: there is nothing to match against.")
    return json.dumps(
        {
            "job_title": job_title.strip() or "the role",
            "skill_types": [
                {"name": name, "levels": list(LEVELS), "skills": skills}
                for name, skills in groups.items()
            ],
        },
        ensure_ascii=False,
    )


def _line(row: Any, status: str) -> Line | None:
    if not isinstance(row, Mapping):
        return None
    skill = str(row.get("skill") or "").strip()
    wanted = str(row.get("required_level") or "").strip()
    if not skill or not wanted:
        return None
    found = row.get("detected_level")
    evidence = row.get("evidence")
    return Line(
        skill=skill,
        category=str(row.get("category") or DEFAULT_CATEGORY).strip(),
        wanted=wanted,
        found=None if found is None else str(found).strip(),
        evidence=None if evidence is None else str(evidence).strip()[:300] or None,
        status=status,
    )


def parse(body: bytes) -> MatchReport:
    """The match report, or nothing. A percentage we cannot tie to skills is not recorded."""
    try:
        answer = json.loads(body)
    except (ValueError, TypeError):
        raise MatchUnreadable("The CV service answered something that is not JSON.") from None
    report = answer.get("match_report") if isinstance(answer, Mapping) else None
    if not isinstance(report, Mapping):
        raise MatchUnreadable("The CV service answered without a match report.")
    percentage = report.get("overall_match_percentage")
    if not isinstance(percentage, int | float) or not 0 <= float(percentage) <= 100:
        raise MatchUnreadable("The match report has no percentage between 0 and 100.")
    lines: list[Line] = []
    for status in ("matched", "below", "missing"):
        rows = report.get(status)
        if isinstance(rows, Sequence) and not isinstance(rows, str | bytes):
            lines.extend(line for line in (_line(row, status) for row in rows) if line)
    if not lines:
        raise MatchUnreadable("The match report names no skill: a percentage alone says nothing.")
    metadata = answer.get("metadata")
    metadata = metadata if isinstance(metadata, Mapping) else {}
    return MatchReport(
        percentage=round(float(percentage), 2),
        lines=tuple(lines),
        job_title=str(report.get("job_title") or "").strip(),
        method=str(metadata.get("extraction_method") or "").strip()[:60],
        requirements_hash=str(metadata.get("requirements_hash") or "").strip()[:40],
    )


class Matcher:
    """The CV service, asked to grade one CV against one job's skills."""

    name = "cv-matcher"

    def __init__(
        self,
        base_url: str,
        *,
        api_key: str = "",
        timeout_seconds: float = 120.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._url = base_url.rstrip("/") + PATH
        self._headers = {"Accept": "application/json"}
        if api_key:
            self._headers[API_KEY_HEADER] = api_key
        self._client = httpx.Client(
            timeout=timeout_seconds, follow_redirects=False, transport=transport
        )

    def match(self, content: bytes, media_type: str, requirements: str) -> MatchReport:
        # The service picks its reader from the extension, so the name is built from the type we
        # sniffed ourselves — never the candidate's own filename (intake.ocr_http says the same).
        filename = f"cv.{EXTENSION.get(media_type, 'bin')}"
        try:
            response = self._client.post(
                self._url,
                headers=self._headers,
                files={FILE_PART: (filename, content, media_type)},
                data={REQUIREMENTS_PART: requirements},
            )
        except httpx.TimeoutException:
            raise OcrTimeout("The CV service did not answer in time.") from None
        except httpx.HTTPError:
            raise OcrUnavailable("The CV service could not be reached.") from None

        status = response.status_code
        if status == 200:
            return parse(response.content)
        if status in RETRYABLE_STATUS or status >= 500:
            raise OcrUnavailable(f"The CV service answered {status}.")
        if status in {401, 403}:
            # Nothing is wrong with the CV: our key is. It waits, and is matched once it is fixed.
            raise OcrUnavailable(
                f"The CV service refused our key ({status}). Check TALENT_OCR_API_KEY."
            )
        raise OcrRejected(f"The CV service refused the file with {status}.")

    def close(self) -> None:
        self._client.close()
