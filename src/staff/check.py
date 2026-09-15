"""Own-staff check: the migrated candidates against the employee roster (BR-301, DEP-04).

    python -m staff.check --out DIR (--crm | --roster FILE)
        [--master "$TALENT_MASTER_PATH"] [--report-copy docs/migration/OWN_STAFF_REPORT.md]

The roster comes from the company CRM (--crm, TALENT_CRM_BASE_URL and TALENT_CRM_SERVICE_KEY) or
from a file HRIS sends (--roster, TALENT_EMPLOYEES_PATH). With neither flag, the CRM is used when
it is configured, then the file.

Criteria version 2026-08-04 excludes a candidate whose current employer or title names The Address.
This checks those exclusions against the active employees, and looks for active employees the text
missed:

  confirmed  the candidate's phone or email equals an active employee's
  possible   only the full name matches, so a person decides (BR-206); nothing is inferred
  none       no active employee matches

Nothing is excluded, re-scored or changed here. The report holds counts and sheet row numbers only.
The per-row file puts employee ids beside sheet rows, so it goes outside the repository, owner-only.
Exit status 1 when an active employee is found whom the text did not exclude.
"""

import argparse
import csv
import io
import os
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from importer.paths import DataLocationError, check_outside_repository, master_path
from replay.mapping import text
from replay.workbook import MasterSheet, WorkbookError, read_master
from scoring.rulesets import v2026_08_04 as ruleset
from staff.crm import CrmError, fetch_roster
from staff.identity import email_key, name_key, phone_key
from staff.roster import Roster, RosterError, read_roster

CONFIRMED = "confirmed"
POSSIBLE = "possible"
NONE = "none"
MATCHES = (CONFIRMED, POSSIBLE, NONE)
ROW_COLUMNS = (
    "sheet_row",
    "excluded_by_text",
    "match",
    "matched_on",
    "employee_ids",
    "stored_score",
    "stored_tier",
    "checked_by",
    "note",
)


@dataclass(frozen=True, slots=True)
class RowCheck:
    sheet_row: int
    excluded_by_text: bool
    employer_is_exactly_the_address: bool
    match: str
    matched_on: str
    employee_ids: tuple[str, ...]
    stored_score: str
    stored_tier: str


def excluded_by_text(values: dict[str, Any]) -> bool:
    """The ruleset's own-staff signal: current employer and current title only."""
    current = ruleset._normalise(f"{text(values.get('Employer'))} {text(values.get('Title'))}")
    return any(signal in current for signal in ruleset.OWN_COMPANY_SIGNALS)


def _index(roster: Roster, attribute: str) -> dict[str, list[str]]:
    found: dict[str, list[str]] = {}
    for employee in roster.active:
        key = getattr(employee, attribute)
        if key:
            found.setdefault(key, []).append(employee.employee_id)
    return found


def check(sheet: MasterSheet, roster: Roster) -> list[RowCheck]:
    by_phone, by_email, by_name = (
        _index(roster, attribute) for attribute in ("phone", "email", "name")
    )
    checks: list[RowCheck] = []
    for row in sheet.rows:
        values = row.values
        keys = (
            ("phone", phone_key(values.get("Phone Number")), by_phone),
            ("email", email_key(values.get("Email")), by_email),
        )
        confirmed = [(field, table[key]) for field, key, table in keys if key and key in table]
        name = name_key(values.get("Name"))
        if confirmed:
            match = CONFIRMED
            matched_on = "+".join(field for field, _ in confirmed)
            ids = tuple(sorted({i for _, found in confirmed for i in found}))
        elif name and name in by_name:
            match, matched_on, ids = POSSIBLE, "name", tuple(sorted(by_name[name]))
        else:
            match, matched_on, ids = NONE, "", ()
        checks.append(
            RowCheck(
                sheet_row=row.sheet_row,
                excluded_by_text=excluded_by_text(values),
                employer_is_exactly_the_address=text(values.get("Employer")).casefold()
                == "the address",
                match=match,
                matched_on=matched_on,
                employee_ids=ids,
                stored_score=text(values.get("Score")),
                stored_tier=text(values.get("Tier")),
            )
        )
    return checks


def summarise(checks: list[RowCheck], roster: Roster, source: str = "file") -> dict[str, Any]:
    excluded = [c for c in checks if c.excluded_by_text]
    kept = [c for c in checks if not c.excluded_by_text]
    return {
        "rows": len(checks),
        "roster": {
            "source": source,
            "employees": len(roster.employees),
            "active": len(roster.active),
            "rows_without_id": roster.rows_without_id,
            # A masked or missing phone or email cannot confirm anyone: say how many can.
            **{
                f"with_{key}": sum(getattr(e, key) is not None for e in roster.active)
                for key in ("phone", "email", "name")
            },
        },
        "excluded_by_text": {
            "rows": len(excluded),
            "stored_score_0": sum(c.stored_score == "0" for c in excluded),
            **{m: sum(c.match == m for c in excluded) for m in MATCHES},
        },
        "not_excluded": {
            "rows": len(kept),
            **{m: sum(c.match == m for c in kept) for m in MATCHES},
        },
        "employees_not_excluded": [c.sheet_row for c in kept if c.match == CONFIRMED],
        "possible_to_check": [c.sheet_row for c in checks if c.match == POSSIBLE],
        "the_address_exactly": [
            (c.sheet_row, c.match) for c in checks if c.employer_is_exactly_the_address
        ],
        "matched_on": dict(Counter(c.matched_on for c in checks if c.matched_on)),
    }


def render_report(summary: dict[str, Any], workbook_sha256: str) -> str:
    excluded, kept, roster = summary["excluded_by_text"], summary["not_excluded"], summary["roster"]
    missed = summary["employees_not_excluded"]
    lines = [
        "# Own-staff check against the employee roster (BR-301)",
        "",
        f"Workbook SHA-256 `{workbook_sha256}`. Counts and sheet rows only; "
        "no candidate or employee values. Nothing was excluded or changed by this check.",
        "",
        f"Roster from the {roster['source']}: {roster['employees']:,} employees, "
        f"{roster['active']:,} active, {roster['rows_without_id']:,} without an employee id "
        f"(not used). Active employees with a usable mobile: {roster['with_phone']:,}, "
        f"email: {roster['with_email']:,}, full name: {roster['with_name']:,}.",
        "",
        "## Candidates the criteria excluded as own staff",
        "",
        "| Rows | Stored score 0 | Confirmed employee | Name matches only | No match |",
        "|---:|---:|---:|---:|---:|",
        f"| {excluded['rows']:,} | {excluded['stored_score_0']:,} | {excluded[CONFIRMED]:,} "
        f"| {excluded[POSSIBLE]:,} | {excluded[NONE]:,} |",
        "",
        "## Candidates the criteria did not exclude",
        "",
        "| Rows | Confirmed employee | Name matches only | No match |",
        "|---:|---:|---:|---:|",
        f"| {kept['rows']:,} | {kept[CONFIRMED]:,} | {kept[POSSIBLE]:,} | {kept[NONE]:,} |",
        "",
        "**Active employees the criteria did not exclude:** "
        + (", ".join(f"sheet row {r}" for r in missed) if missed else "none")
        + ". Each needs a ruling; none is excluded automatically.",
        "",
        "**Name-only matches to check by hand (BR-206):** "
        + (", ".join(str(r) for r in summary["possible_to_check"]) or "none")
        + ".",
        "",
        '**Employer exactly "The Address" (OPN-03):** '
        + (", ".join(f"sheet row {r} ({m})" for r, m in summary["the_address_exactly"]) or "none")
        + ".",
        "",
    ]
    return "\n".join(lines)


def _write_private(path: Path, content: str) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as handle:
        handle.write(content)
    os.chmod(path, 0o600)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m staff.check", description=__doc__.splitlines()[0]
    )
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--crm", action="store_true", help="read active employees from the CRM")
    source.add_argument("--roster", help="a roster file (default: $TALENT_EMPLOYEES_PATH)")
    parser.add_argument("--master", help="the workbook (default: $TALENT_MASTER_PATH)")
    parser.add_argument("--out", type=Path, required=True, help="outside any git repository")
    parser.add_argument(
        "--report-copy", type=Path, help="counts and sheet rows only, so it may be committed"
    )
    args = parser.parse_args(argv)

    crm_url = os.environ.get("TALENT_CRM_BASE_URL", "")
    crm_key = os.environ.get("TALENT_CRM_SERVICE_KEY", "")
    roster_file = args.roster or os.environ.get("TALENT_EMPLOYEES_PATH")
    use_crm = args.crm or (not args.roster and bool(crm_url and crm_key))
    if not use_crm and not roster_file:
        print(
            "error: no roster. Set TALENT_CRM_BASE_URL and TALENT_CRM_SERVICE_KEY for the CRM, "
            "or pass --roster with the file HRIS sends the employee roster in (DEP-04).",
            file=sys.stderr,
        )
        return 2
    out_dir = args.out.expanduser()
    try:
        check_outside_repository(out_dir, None, "--out")
        if not use_crm:
            roster_path = Path(str(roster_file)).expanduser()
            check_outside_repository(roster_path, None, roster_path.name)
        sheet = read_master(master_path(args.master))
        roster = fetch_roster(crm_url, crm_key) if use_crm else read_roster(roster_path)
    except (DataLocationError, WorkbookError, RosterError, CrmError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    checks = check(sheet, roster)
    summary = summarise(checks, roster, "CRM" if use_crm else "roster file")
    report = render_report(summary, sheet.sha256)

    out_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=ROW_COLUMNS)
    writer.writeheader()
    for c in checks:
        if c.excluded_by_text or c.match != NONE:
            writer.writerow(
                {
                    "sheet_row": c.sheet_row,
                    "excluded_by_text": "yes" if c.excluded_by_text else "no",
                    "match": c.match,
                    "matched_on": c.matched_on,
                    "employee_ids": " ".join(c.employee_ids),
                    "stored_score": c.stored_score,
                    "stored_tier": c.stored_tier,
                    "checked_by": "",
                    "note": "",
                }
            )
    stem = f"own-staff-{sheet.sha256[:12]}"
    _write_private(out_dir / f"{stem}-rows.csv", buffer.getvalue())
    _write_private(out_dir / f"{stem}.md", report)
    if args.report_copy is not None:
        args.report_copy.parent.mkdir(parents=True, exist_ok=True)
        args.report_copy.write_text(report, encoding="utf-8")

    print(
        f"Excluded as own staff: {summary['excluded_by_text']['rows']}. "
        f"Active employees not excluded: {len(summary['employees_not_excluded'])}. "
        f"Name-only matches to check: {len(summary['possible_to_check'])}."
    )
    if roster.active and not (summary["roster"]["with_phone"] or summary["roster"]["with_email"]):
        print(
            "warning: no active employee has a mobile or email, so no candidate can be confirmed; "
            "only full-name matches are possible."
        )
    print(f"  report  {out_dir / (stem + '.md')}")
    print(f"  rows    {out_dir / (stem + '-rows.csv')}  (employee ids, never commit)")
    return 1 if summary["employees_not_excluded"] else 0


if __name__ == "__main__":
    sys.exit(main())
