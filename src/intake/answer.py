"""The answer the platform expects from the OCR (A1, BR-309, BR-308). docs/intake/OCR_ANSWER.md.

GUESS: built from made-up samples, not from the OCR team's answers. When real answers arrive,
this module and its samples change; the mapping in intake.cv_fields reads only OcrAnswer.

    {
      "schema": "...",                                  optional, recorded if present
      "document": {"language": "ar" | "en" | "mixed", "pages": 2},
      "fields": {
        "full_name": {"text": "...", "language": "ar", "confidence": 0.97},
        ...                                             any of FIELD_NAMES; others are ignored
      },
      "hidden_content": {"found": true, "kinds": ["white_text"], "removed": true}
    }

`text` is what the CV says, as written, after the OCR removed any hidden content. Nothing here is
translated or tidied: that is the mapping's job, and it never translates.
"""

import json
import unicodedata
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

SCHEMA_GUESS = "talent-ocr-answer/guess-2026-09-16"

# The fields a CV answer may carry, in form order.
FIELD_NAMES: tuple[str, ...] = (
    "full_name",
    "phone",
    "whatsapp",
    "email",
    "location",
    "current_title",
    "current_employer",
    "education",
    "graduation_year",
    "years_experience",
    "age",
    "date_of_birth",
    "profile_url",
)

Language = Literal["ar", "en", "mixed"]
MAX_TEXT = 2000


class AnswerUnreadable(Exception):
    """The answer is not in the expected shape. The message names the problem, never the text."""


class AnswerField(BaseModel):
    model_config = ConfigDict(extra="ignore")

    text: str = Field(max_length=MAX_TEXT)
    language: Language | None = None
    confidence: float | None = Field(default=None, ge=0, le=1)


class HiddenContent(BaseModel):
    model_config = ConfigDict(extra="ignore")

    found: bool = False
    # Codes such as white_text, tiny_font, instructions. Never the hidden text itself.
    kinds: list[str] = Field(default_factory=list, max_length=20)
    removed: bool = False


class AnswerDocument(BaseModel):
    model_config = ConfigDict(extra="ignore")

    language: Language | None = None
    pages: int | None = Field(default=None, ge=0)


class OcrAnswer(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    answer_schema: str | None = Field(default=None, alias="schema", max_length=100)
    document: AnswerDocument = Field(default_factory=AnswerDocument)
    fields: dict[str, AnswerField] = Field(default_factory=dict)
    hidden_content: HiddenContent = Field(default_factory=HiddenContent)

    def known_fields(self) -> dict[str, AnswerField]:
        return {name: self.fields[name] for name in FIELD_NAMES if name in self.fields}

    def unknown_field_count(self) -> int:
        return sum(1 for name in self.fields if name not in FIELD_NAMES)


def parse_answer(body: bytes) -> OcrAnswer:
    """The answer, checked. Raises AnswerUnreadable naming what is wrong, never quoting it.

    Two shapes arrive here: this one, and the company OCR service's own (intake.cv_extractor),
    which is translated into this one. The answer is always stored exactly as it came, whichever
    it is, so a change of reader never rewrites what a CV said.
    """
    from intake.cv_extractor import parse as parse_cv_extractor

    translated = parse_cv_extractor(body)
    if translated is not None:
        return translated
    try:
        decoded = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise AnswerUnreadable("the answer is not UTF-8 JSON") from None
    if not isinstance(decoded, dict):
        raise AnswerUnreadable("the answer is not a JSON object")
    try:
        return OcrAnswer.model_validate(decoded)
    except ValidationError as exc:
        places = sorted({".".join(str(part) for part in error["loc"]) for error in exc.errors()})
        raise AnswerUnreadable(f"the answer has invalid parts: {', '.join(places)}") from None


def _is_arabic(ch: str) -> bool:
    return "؀" <= ch <= "ۿ" or "ݐ" <= ch <= "ݿ" or "ﭐ" <= ch <= "﻿"


def language_of(text: str) -> Language | None:
    """ar, en or mixed, from the letters used. None when the text has no letters."""
    arabic = latin = False
    for ch in text:
        if _is_arabic(ch) and unicodedata.category(ch).startswith("L"):
            arabic = True
        elif ch.isascii() and ch.isalpha():
            latin = True
    if arabic and latin:
        return "mixed"
    if arabic:
        return "ar"
    if latin:
        return "en"
    return None
