"""One TAI_Master row, turned into what the import stores. Pure functions: no database, no storage.

Follows docs/migration/COLUMN_MAP.md:

  RAW        every cell of the row, typed, is kept as one raw capture. It is keyed by sheet row and
             a hash of the cells alone, so re-saving the workbook without changing a cell gives the
             same capture. The workbook file itself is kept as its own capture.
  M-PROV     profile columns become candidate fields: source, source_ref, unverified
  INF?       Age and Years Exp may have been derived, so their inference is "unknown" (A4): the
             workbook cannot show whether a value was stated or guessed
  HIST-EVAL  a stored score becomes an evaluation under 2026-08-04, copied, never recomputed (A2).
             The original Signals and Flags text is kept beside the split lists.
  NR         a blank cell, or a "no value" marker in a column that has them, is "not recorded"

Pipeline and outreach columns have no table until week 3, and most wait on a ruling, so they stay
in the raw capture only. So does any cell outside the named columns.
"""

import hashlib
import json
from dataclasses import dataclass
from datetime import date, datetime, time
from typing import Any

from replay.mapping import number, split_items
from replay.workbook import MasterRow, MasterSheet

SOURCE = "tai_master"
FIELD_SOURCE = "migrated from TAI_Master"
CRITERIA_VERSION = "2026-08-04"

UNVERIFIED = "unverified"
NOT_RECORDED = "not_recorded"
INFERENCE_UNKNOWN = "unknown"

# (column, field, inference). None: copied as written. "unknown": the sheet cannot show whether
# the value was stated by the candidate or derived (OPN-02, Q-19).
FIELD_COLUMNS: tuple[tuple[str, str, str | None], ...] = (
    ("Name", "full_name", None),
    ("Age", "age", INFERENCE_UNKNOWN),
    ("Title", "current_title", None),
    ("Employer", "current_employer", None),
    ("Location", "location", None),
    ("Phone Number", "phone", None),
    ("Email", "email", None),
    ("Profile URL", "profile_url", None),
    ("Education", "education", None),
    ("Years Exp", "years_experience", INFERENCE_UNKNOWN),
    ("Last Active", "source_last_active", None),
    ("Platform", "source_platform", None),
    ("Date Added", "date_added", None),
)

# Cell text that means "no value" in these columns (COLUMN_MAP F-05), compared ignoring case and
# surrounding spaces. The number columns use the replay's markers plus the Arabic question mark.
_ARABIC_QUESTION_MARK = "؟"
_NUMBER_MARKERS = frozenset({"?", _ARABIC_QUESTION_MARK, "-", "—", "n/a", "na", "none", "unknown"})
NO_VALUE_MARKERS: dict[str, frozenset[str]] = {
    "Age": _NUMBER_MARKERS | {"0"},  # no candidate is 0 years old: the source had no age
    "Years Exp": _NUMBER_MARKERS,
    "Last Active": frozenset({"?", _ARABIC_QUESTION_MARK}),
}

SCORE = "Score"
TIER = "Tier"
RECOMMENDATION = "Recommendation"
SIGNALS = "Signals (Reasons to call)"
FLAGS = "Flags (reasons of disqualification)"
CALL_PRIORITY = "Call Priority"
EVALUATION_COLUMNS = (SCORE, TIER, RECOMMENDATION, SIGNALS, FLAGS, CALL_PRIORITY)

# Column: what it waits on before it gets a structured home.
RAW_ONLY_COLUMNS: dict[str, str] = {
    "WhatsApp Invite Sent": "Q-07",
    "Stage": "Q-01 and the week 3 stage list",
    "Phone Screen Result": "Q-01",
    "Interview Scheduled": "Q-21",
    "HR Interview Date & Time": "Q-21",
    "HR Interview WA Sent": "OPN-01",
    "HR Interview Result": "the week 3 pipeline tables",
    "HR Supervisor Result": "Q-16",
    "IQ Sent": "Q-06",
    "IQ Completed": "Q-06",
    "IQ Score": "Q-06",
    "IQ Band": "Q-06",
    "Technical Interview": "the week 3 pipeline tables",
    "Attempt #": "Q-03",
    "Sales Team": "Q-04",
    "Job Offer Sent": "the week 3 pipeline tables",
    "On Floor Date": "Q-02",
    "Hired ✓": "Q-02",
    "Hired Date": "Q-02",
    "Rejection Stage": "the week 3 pipeline tables",
    "Rejection Reason": "the TA rejection reason list",
    "HR Feedback": "Q-15",
    "WA First Contact Sent": "Q-07",
    "WA Reply": "Q-08",
    "WA Reply Date": "Q-08",
    "Contact Decision": "Q-09",
    "Assigned Recruiter": "Q-18",
    "Notion Page ID": "Q-05",
}

# Every column the import reads. A sheet missing any of them is refused, never read as blank.
IMPORT_COLUMNS: tuple[str, ...] = (
    *(column for column, _, _ in FIELD_COLUMNS),
    *EVALUATION_COLUMNS,
    *RAW_ONLY_COLUMNS,
)
UNNAMED_COLUMN_PREFIX = "(column "

SCORE_NOT_A_WHOLE_NUMBER = "stored_score_not_a_whole_number"
SCORE_OUT_OF_RANGE = "stored_score_outside_0_to_100"
PLATFORM_TEST_ROW = "platform_test_row_awaiting_q13"


@dataclass(frozen=True, slots=True)
class FieldValue:
    field: str
    column: str
    value: str | None
    status: str
    inference: str | None


@dataclass(frozen=True, slots=True)
class StoredEvaluation:
    score: int
    tier: str | None
    recommendation: str | None
    signals: tuple[str, ...] | None
    signals_text: str | None
    flags: tuple[str, ...] | None
    flags_text: str | None
    call_priority: str | None


@dataclass(frozen=True, slots=True)
class PreparedRow:
    sheet_row: int
    source_key: str
    external_id: str
    payload: bytes
    payload_sha256: bytes
    fields: tuple[FieldValue, ...]
    evaluation: StoredEvaluation | None
    raw_only_filled: tuple[str, ...]
    unnamed_filled: tuple[str, ...]
    unresolved: tuple[str, ...]


def missing_columns(sheet: MasterSheet) -> list[str]:
    return [column for column in IMPORT_COLUMNS if column not in sheet.columns]


def is_blank(value: Any) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


def cell_text(value: Any) -> str:
    """A cell as text, without reinterpreting it. 27.0 stored by the sheet reads as 27."""
    if isinstance(value, datetime | date | time):
        return value.isoformat()
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def is_no_value(column: str, value: Any) -> bool:
    markers = NO_VALUE_MARKERS.get(column)
    return markers is not None and cell_text(value).strip().lower() in markers


def _typed(value: Any) -> dict[str, Any] | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return {"type": "bool", "value": value}
    if isinstance(value, int):
        return {"type": "int", "value": value}
    if isinstance(value, float):
        return {"type": "float", "value": repr(value)}
    if isinstance(value, datetime):
        return {"type": "datetime", "value": value.isoformat()}
    if isinstance(value, date):
        return {"type": "date", "value": value.isoformat()}
    if isinstance(value, time):
        return {"type": "time", "value": value.isoformat()}
    return {"type": "str", "value": str(value)}


def raw_payload(row: MasterRow) -> bytes:
    """Every cell of the row in workbook order, blanks included, and nothing about the file.

    The same cells give the same bytes however often the workbook is re-saved or re-exported.
    """
    body = {
        "sheet_row": row.sheet_row,
        "cells": [[column, _typed(value)] for column, value in row.values.items()],
    }
    encoded = json.dumps(body, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    return encoded.encode("utf-8")


def _field(values: dict[str, Any], column: str, name: str, inference: str | None) -> FieldValue:
    value = values.get(column)
    if is_blank(value) or is_no_value(column, value):
        return FieldValue(name, column, None, NOT_RECORDED, None)
    return FieldValue(name, column, cell_text(value), UNVERIFIED, inference)


def _optional_text(value: Any) -> str | None:
    return None if is_blank(value) else cell_text(value)


def _items(value: Any) -> tuple[str, ...] | None:
    # A blank Signals or Flags cell stays blank: whether it means "none" waits on Q-12.
    return None if is_blank(value) else split_items(value)


def _stored_evaluation(values: dict[str, Any]) -> tuple[StoredEvaluation | None, str | None]:
    if is_blank(values.get(SCORE)):
        return None, None
    score = number(values.get(SCORE), SCORE, [])
    if score is None or not score.is_integer():
        return None, SCORE_NOT_A_WHOLE_NUMBER
    if not 0 <= score <= 100:
        return None, SCORE_OUT_OF_RANGE
    evaluation = StoredEvaluation(
        score=int(score),
        tier=_optional_text(values.get(TIER)),
        recommendation=_optional_text(values.get(RECOMMENDATION)),
        signals=_items(values.get(SIGNALS)),
        signals_text=_optional_text(values.get(SIGNALS)),
        flags=_items(values.get(FLAGS)),
        flags_text=_optional_text(values.get(FLAGS)),
        call_priority=_optional_text(values.get(CALL_PRIORITY)),
    )
    return evaluation, None


def prepare_row(row: MasterRow, source: str = SOURCE) -> PreparedRow:
    values = row.values
    payload = raw_payload(row)
    evaluation, problem = _stored_evaluation(values)
    unresolved = [problem] if problem else []
    if cell_text(values.get("Platform")).strip() == "Test":
        unresolved.append(PLATFORM_TEST_ROW)
    return PreparedRow(
        sheet_row=row.sheet_row,
        source_key=f"{source}:row:{row.sheet_row}",
        external_id=f"row:{row.sheet_row}",
        payload=payload,
        payload_sha256=hashlib.sha256(payload).digest(),
        fields=tuple(_field(values, column, name, inf) for column, name, inf in FIELD_COLUMNS),
        evaluation=evaluation,
        raw_only_filled=tuple(c for c in RAW_ONLY_COLUMNS if not is_blank(values.get(c))),
        unnamed_filled=tuple(
            c for c in values if c.startswith(UNNAMED_COLUMN_PREFIX) and not is_blank(values[c])
        ),
        unresolved=tuple(unresolved),
    )
