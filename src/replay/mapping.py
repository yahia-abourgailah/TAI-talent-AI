"""Turns a master-workbook row into the ruleset's Candidate.

    Column          Candidate field
    Name            full_name
    Title           current_title
    Employer        current_employer
    Location        location
    Phone Number    phone_number
    Email           email
    Profile URL     profile_url
    Education       education_level
    Years Exp       years_experience   a number; unreadable values are recorded and left empty
    Age             age                a whole number; "?" and blanks are left empty
    Platform        source_platform    lower-cased

Last Active is deliberately not passed. The stored scores were produced without it: replayed
without it, 3,938 of 3,940 stored scores match exactly; with it, only 1,880 do. Recency is not in
CRITERIA.md either (docs/criteria/DIFFERENCES.md, D-04 and D-14).

The workbook holds none of: skills, summary, raw profile text, graduation year, student status,
profile photo, connection count, open-to-work, tenure or move signal. They stay at the ruleset's
defaults, as they evidently did when the stored scores were produced.
"""

import hashlib
import json
import math
from dataclasses import asdict, dataclass
from typing import Any

from replay.workbook import MasterRow
from scoring.rulesets.v2026_08_04 import Candidate

_UNKNOWN_MARKERS = {"", "?", "-", "—", "n/a", "na", "none", "unknown"}


@dataclass(frozen=True, slots=True)
class MappedRow:
    candidate: Candidate
    mode: str
    input_sha256: str
    parse_issues: tuple[str, ...]


def text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def number(value: Any, column: str, issues: list[str]) -> float | None:
    """A cell as a number. Unknown markers are empty; anything else unreadable is recorded."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        parsed = float(value)
    else:
        raw = str(value).strip()
        if raw.lower() in _UNKNOWN_MARKERS:
            return None
        try:
            parsed = float(raw.replace(",", ""))
        except ValueError:
            issues.append(column)
            return None
    if not math.isfinite(parsed):
        issues.append(column)
        return None
    return parsed


def split_items(value: Any) -> tuple[str, ...]:
    """Signals and flags are stored in one cell, separated by '; '."""
    return tuple(part.strip() for part in text(value).split("; ") if part.strip())


def track(values: dict[str, Any]) -> str:
    """Track B rows carry T tiers. Unscored rows are treated as Track A."""
    return "headhunt" if text(values.get("Tier")).upper().startswith("T") else "entry"


def map_row(row: MasterRow) -> MappedRow:
    values = row.values
    issues: list[str] = []
    years = number(values.get("Years Exp"), "Years Exp", issues)
    age = number(values.get("Age"), "Age", issues)
    candidate = Candidate(
        full_name=text(values.get("Name")),
        current_title=text(values.get("Title")),
        current_employer=text(values.get("Employer")),
        location=text(values.get("Location")),
        phone_number=text(values.get("Phone Number")),
        email=text(values.get("Email")),
        profile_url=text(values.get("Profile URL")),
        education_level=text(values.get("Education")),
        years_experience=years,
        age=None if age is None else int(age),
        source_platform=text(values.get("Platform")).lower(),
    )
    encoded = json.dumps(asdict(candidate), sort_keys=True, ensure_ascii=False).encode("utf-8")
    return MappedRow(
        candidate=candidate,
        mode=track(values),
        input_sha256=hashlib.sha256(encoded).hexdigest(),
        parse_issues=tuple(issues),
    )
