"""An OCR answer, turned into candidate fields (A1: BR-309, NFR-07, BR-201, BR-703).

Each field becomes one core.candidate_field row: source cv_extraction, unverified, with the
language it was written in. The rules:

  Text as written   Names, titles, employers, places, phones and emails keep the CV's own text,
                    only trimmed at the ends. A name is never translated, transliterated or
                    re-spelled. Tidying (Arabic-Indic digits, phone formats) is for matching only:
                    tidy_digits and tidy_phone, never stored in place of the text.
  Numbers           age, years_experience and graduation_year are read as numbers, Arabic-Indic
                    digits included, and stored as plain digits. Anything that is not a plain
                    number ("3+ years", "twenty") is not recorded and counted as unreadable.
  No guesses        A field the CV does not have is not recorded. So is an empty text. An age of 0
                    is no age.
  Inferred          A value worked out rather than read says so. Age comes from, in order: the age
                    the CV states (stated); the date of birth (inferred); the graduation year, with
                    the criteria's own rule (inferred, OPN-02). Years of experience are added up
                    from the jobs' own dates when the CV gives no total (inferred; the reader says
                    which). Nothing else is inferred.

The date of birth itself is not stored: only the age worked out from it.
"""

import re
from dataclasses import dataclass, field
from datetime import date

from intake.answer import AnswerField, Language, OcrAnswer, language_of
from scoring.rulesets.v2026_08_04 import CURRENT_YEAR, Candidate
from scoring.rulesets.v2026_08_04 import _infer_age as ruleset_infer_age

SOURCE = "cv_extraction"
UNVERIFIED = "unverified"
NOT_RECORDED = "not_recorded"
STATED = "stated"
INFERRED = "inferred"

# What the form shows, in order. date_of_birth is read but never stored.
STORED_FIELDS: tuple[str, ...] = (
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
    "profile_url",
)
TEXT_FIELDS = frozenset(
    {
        "full_name",
        "phone",
        "whatsapp",
        "email",
        "location",
        "current_title",
        "current_employer",
        "education",
        "profile_url",
    }
)

MIN_AGE, MAX_AGE = 14, 90
MAX_YEARS_EXPERIENCE = 60
EARLIEST_GRADUATION = 1950

# Arabic-Indic and extended Arabic-Indic digits, and the Arabic decimal separator.
_OTHER_DIGITS = "".join(chr(0x0660 + i) for i in range(10)) + "".join(
    chr(0x06F0 + i) for i in range(10)
)
_ARABIC_DECIMAL_SEPARATOR = chr(0x066B)
_DIGITS = str.maketrans(_OTHER_DIGITS + _ARABIC_DECIMAL_SEPARATOR, "0123456789" * 2 + ".")
_PLAIN_NUMBER = re.compile(r"[0-9]{1,4}(\.[0-9]{1,2})?")
_ISO_DATE = re.compile(r"([0-9]{4})-([0-9]{2})-([0-9]{2})")
_DMY_DATE = re.compile(r"([0-9]{1,2})[/.-]([0-9]{1,2})[/.-]([0-9]{4})")


@dataclass(frozen=True, slots=True)
class CvField:
    field: str
    value: str | None
    status: str
    inference: str | None
    language: Language | None


@dataclass(slots=True)
class Mapped:
    fields: list[CvField]
    # Field names whose text could not be read as the number it should be. Never the text.
    unreadable: list[str] = field(default_factory=list)
    hidden_content: bool = False
    ignored_fields: int = 0

    def by_name(self) -> dict[str, CvField]:
        return {f.field: f for f in self.fields}


def tidy_digits(text: str) -> str:
    """Arabic-Indic digits as 0-9. For matching and for reading numbers only."""
    return text.translate(_DIGITS)


def tidy_phone(text: str) -> str:
    """A phone number for matching: its last 10 digits, so +20 100..., 0020 100... and 0100...
    are the same. Shorter numbers keep every digit."""
    digits = "".join(ch for ch in tidy_digits(text) if ch.isdigit())
    return digits[-10:]


def _not_recorded(name: str) -> CvField:
    return CvField(name, None, NOT_RECORDED, None, None)


def _text(name: str, found: AnswerField | None) -> CvField:
    if found is None or not found.text.strip():
        return _not_recorded(name)
    value = found.text.strip()
    return CvField(name, value, UNVERIFIED, STATED, found.language or language_of(value))


def _number(found: AnswerField | None) -> float | None:
    """The field's text as a plain number, or None. Raises ValueError when it is not one."""
    if found is None or not found.text.strip():
        return None
    raw = tidy_digits(found.text.strip())
    if not _PLAIN_NUMBER.fullmatch(raw):
        raise ValueError
    return float(raw)


def _digits(value: float) -> str:
    return str(int(value)) if value.is_integer() else str(value)


def _date_of_birth(found: AnswerField | None) -> date | None:
    """A written date of birth, if it is a whole, unambiguous date. Raises ValueError otherwise."""
    if found is None or not found.text.strip():
        return None
    raw = tidy_digits(found.text.strip())
    iso = _ISO_DATE.fullmatch(raw)
    if iso:
        return date(int(iso[1]), int(iso[2]), int(iso[3]))
    dmy = _DMY_DATE.fullmatch(raw)
    if dmy:  # Egypt writes day first
        return date(int(dmy[3]), int(dmy[2]), int(dmy[1]))
    raise ValueError


def _years_between(born: date, today: date) -> int:
    return today.year - born.year - ((today.month, today.day) < (born.month, born.day))


def _age_from_graduation(graduation_year: int, education: str) -> int | None:
    """The criteria's own rule (OPN-02), so the platform and the scorer cannot disagree."""
    probe = Candidate(graduation_year=graduation_year, education_level=education)
    return ruleset_infer_age(probe)


def map_answer(answer: OcrAnswer, today: date) -> Mapped:
    """Every stored field, recorded or not, in form order."""
    found = answer.known_fields()
    mapped = Mapped(fields=[], ignored_fields=answer.unknown_field_count())
    mapped.hidden_content = answer.hidden_content.found
    values: dict[str, CvField] = {name: _text(name, found.get(name)) for name in TEXT_FIELDS}

    def number(name: str, low: float, high: float) -> float | None:
        try:
            value = _number(found.get(name))
        except ValueError:
            mapped.unreadable.append(name)
            return None
        if value is None or (name == "age" and value == 0):
            return None
        if not low <= value <= high:
            mapped.unreadable.append(name)
            return None
        return value

    def as_field(name: str, value: float | None, inference: str) -> CvField:
        if value is None:
            return _not_recorded(name)
        return CvField(name, _digits(value), UNVERIFIED, inference, None)

    years = number("years_experience", 0, MAX_YEARS_EXPERIENCE)
    said = found.get("years_experience")
    values["years_experience"] = as_field(
        "years_experience",
        years,
        INFERRED if said is not None and said.inference == INFERRED else STATED,
    )

    graduation = number("graduation_year", EARLIEST_GRADUATION, CURRENT_YEAR + 6)
    if graduation is not None and not graduation.is_integer():
        mapped.unreadable.append("graduation_year")
        graduation = None
    values["graduation_year"] = as_field("graduation_year", graduation, STATED)

    age = number("age", MIN_AGE, MAX_AGE)
    if age is not None and not age.is_integer():
        mapped.unreadable.append("age")
        age = None
    age_inference = STATED
    if age is None:
        age_inference = INFERRED
        try:
            born = _date_of_birth(found.get("date_of_birth"))
        except ValueError:
            mapped.unreadable.append("date_of_birth")
            born = None
        if born is not None:
            worked_out = _years_between(born, today)
            age = float(worked_out) if MIN_AGE <= worked_out <= MAX_AGE else None
        if age is None and graduation is not None:
            education = values["education"].value or ""
            worked_out_age = _age_from_graduation(int(graduation), education)
            if worked_out_age is not None and MIN_AGE <= worked_out_age <= MAX_AGE:
                age = float(worked_out_age)
    values["age"] = as_field("age", age, age_inference)

    mapped.fields = [values[name] for name in STORED_FIELDS]
    return mapped
