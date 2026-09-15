"""The employee roster from HRIS, read without changing it (DEP-04).

The file stays outside the repository (TALENT_EMPLOYEES_PATH), as .xlsx or .csv. HRIS has not yet
confirmed its layout, so the headers read here are listed in ROSTER_COLUMNS, and a roster missing
one is refused, never read as blank. When the real headers are known, change ROSTER_COLUMNS and
ACTIVE_STATUSES only.
"""

import csv
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import openpyxl

from staff.identity import email_key, name_key, phone_key

ROSTER_COLUMNS: dict[str, str] = {
    "employee_id": "Employee ID",
    "full_name": "Full Name",
    "mobile": "Mobile",
    "email": "Email",
    "status": "Status",
}
ACTIVE_STATUSES = frozenset({"active"})


class RosterError(Exception):
    """The roster is missing, unreadable, or badly shaped. Names files and columns only."""


@dataclass(frozen=True, slots=True)
class Employee:
    employee_id: str
    active: bool
    phone: str | None
    email: str | None
    name: str | None


@dataclass(frozen=True, slots=True)
class Roster:
    employees: tuple[Employee, ...]
    rows_without_id: int

    @property
    def active(self) -> tuple[Employee, ...]:
        return tuple(employee for employee in self.employees if employee.active)


def _blank(value: Any) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


def _rows(path: Path) -> tuple[Sequence[Any], Iterable[Sequence[Any]]]:
    suffix = path.suffix.lower()
    if suffix == ".csv":
        with path.open(newline="", encoding="utf-8-sig") as handle:
            rows = list(csv.reader(handle))
        return (rows[0] if rows else []), rows[1:]
    if suffix in {".xlsx", ".xlsm"}:
        try:
            workbook = openpyxl.load_workbook(path, read_only=True, data_only=True)
        except Exception as exc:  # openpyxl raises many unrelated types for a damaged file
            raise RosterError(f"Could not open {path.name}: {type(exc).__name__}") from exc
        try:
            cells = list(workbook.worksheets[0].iter_rows(values_only=True))
        finally:
            workbook.close()
        return (cells[0] if cells else []), cells[1:]
    raise RosterError(f"{path.name}: the roster must be an .xlsx or .csv file.")


def read_roster(path: Path) -> Roster:
    if not path.is_file():
        raise RosterError(f"No roster at {path}. Set TALENT_EMPLOYEES_PATH or pass --roster.")
    header, rows = _rows(path)
    columns = ["" if name is None else str(name).strip() for name in header]
    missing = [name for name in ROSTER_COLUMNS.values() if name not in columns]
    if missing:
        raise RosterError(f"The roster is missing columns: {', '.join(missing)}.")
    position = {key: columns.index(name) for key, name in ROSTER_COLUMNS.items()}

    employees: list[Employee] = []
    without_id = 0
    for cells in rows:
        if all(_blank(cell) for cell in cells):
            continue

        def cell(key: str, cells: Sequence[Any] = cells) -> Any:
            index = position[key]
            return cells[index] if index < len(cells) else None

        if _blank(cell("employee_id")):
            without_id += 1
            continue
        employees.append(
            Employee(
                employee_id=str(cell("employee_id")).strip(),
                active=str(cell("status") or "").strip().casefold() in ACTIVE_STATUSES,
                phone=phone_key(cell("mobile")),
                email=email_key(cell("email")),
                name=name_key(cell("full_name")),
            )
        )
    return Roster(tuple(employees), without_id)
