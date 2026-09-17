"""The company's CV OCR service, as it really answers (BR-102, BR-309, CR-01).

Read from the service's own description at https://cv-extractor.mytai.app/openapi.json on
17 September 2026, not from a guess:

    POST /extract            multipart, one part "file"; header X-API-Key
    200  ExtractedCV: {"name", "email", "phone", "address",
                       "experience": [{"role", "company", "duration"}, …],
                       "education":  [{"degree", "institution", "year"}, …],
                       "skills": […], "languages": […], "inferred_skills": […],
                       "links": {"linkedin": {"url", "source", "page"}, …}}

The platform reads one shape (intake.answer.OcrAnswer), so this translates. The rules it follows
are the platform's, not the service's:

  * the newest job is the current one, so experience[0] gives the title and the employer;
  * education[0] gives the degree, and its year is the graduation year — nothing else is inferred
    here, and the age rule stays where it is (intake.cv_fields, OPN-02);
  * `address` is the candidate's location as written: never translated, never re-spelled;
  * a LinkedIn link, if there is one, is the profile;
  * the language of each value is read from its letters, because the service does not say.

**The service reports nothing about hidden content.** BR-308 needs that signal, so the platform
records "not reported" rather than "none found": an absent answer is not a clean bill of health,
and a CV is never marked safe on silence. This is the first question for the OCR team.
"""

import json
from typing import Any

from intake.answer import (
    AnswerField,
    AnswerUnreadable,
    HiddenContent,
    OcrAnswer,
    language_of,
)

SCHEMA = "cv-extractor/1.0.0"
PROFILE_SOURCES = ("linkedin", "github", "portfolio", "website")
MAX_TEXT = 2000


def _looks_like_this_service(payload: dict[str, Any]) -> bool:
    return "fields" not in payload and any(
        key in payload for key in ("name", "experience", "education", "skills", "links")
    )


def _field(value: Any) -> AnswerField | None:
    """One value, as written. Nothing is tidied here beyond the ends of the string."""
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    return AnswerField(text=text[:MAX_TEXT], language=language_of(text))


def _first(items: Any) -> dict[str, Any]:
    if isinstance(items, list) and items and isinstance(items[0], dict):
        return items[0]
    return {}


def _profile(links: Any) -> AnswerField | None:
    if not isinstance(links, dict):
        return None
    for source in PROFILE_SOURCES:
        entry = links.get(source)
        url = entry.get("url") if isinstance(entry, dict) else entry
        found = _field(url)
        if found is not None:
            return found
    return None


def translate(payload: dict[str, Any]) -> OcrAnswer:
    """The service's answer in the shape the platform reads, with values as the CV wrote them."""
    newest_job = _first(payload.get("experience"))
    newest_study = _first(payload.get("education"))
    candidates = {
        "full_name": _field(payload.get("name")),
        "email": _field(payload.get("email")),
        "phone": _field(payload.get("phone")),
        "location": _field(payload.get("address")),
        "current_title": _field(newest_job.get("role")),
        "current_employer": _field(newest_job.get("company")),
        "education": _field(newest_study.get("degree")),
        "graduation_year": _field(newest_study.get("year")),
        "profile_url": _profile(payload.get("links")),
    }
    fields = {name: found for name, found in candidates.items() if found is not None}
    spoken = [str(value) for value in payload.get("languages") or [] if str(value).strip()]
    return OcrAnswer(
        schema=SCHEMA,
        document={"language": _document_language(fields, spoken)},
        fields=fields,
        # Not "no hidden content found": the service does not look, so nobody has said.
        hidden_content=HiddenContent(found=False, kinds=["not_reported"], removed=False),
    )


def _document_language(fields: dict[str, AnswerField], spoken: list[str]) -> str | None:
    """What the CV is written in, from the values themselves: the service does not say."""
    languages = {found.language for found in fields.values() if found.language}
    if not languages:
        return None
    if languages == {"ar"}:
        return "ar"
    if languages == {"en"}:
        return "en"
    return "mixed"


def parse(body: bytes) -> OcrAnswer | None:
    """The answer when it came from this service, or None when it is some other shape."""
    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise AnswerUnreadable("the answer is not UTF-8 JSON") from None
    if not isinstance(payload, dict):
        raise AnswerUnreadable("the answer is not a JSON object")
    if not _looks_like_this_service(payload):
        return None
    return translate(payload)
