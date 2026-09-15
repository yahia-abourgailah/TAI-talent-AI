"""The own-staff check against a roster, on fabricated people only."""

import csv
import stat
from pathlib import Path

import openpyxl
import pytest

from replay.workbook import REQUIRED_COLUMNS, read_master
from staff.check import CONFIRMED, NONE, POSSIBLE, check, main
from staff.identity import email_key, name_key, phone_key
from staff.roster import ROSTER_COLUMNS, RosterError, read_roster

# Fabricated numbers that belong to no one.
MOBILE_A = "010" + "00000001"
MOBILE_B = "011" + "00000002"


@pytest.mark.parametrize(
    "raw",
    [
        MOBILE_A,
        "+20 100 000 0001",
        "0020 100 000 0001",
        "010 0000 0001",
        int(MOBILE_A),  # stored as a number, leading zero dropped
        float(MOBILE_A),
        "\u0660\u0661\u0660" + "\u0660" * 7 + "\u0661",  # Arabic-Indic digits
    ],
)
def test_one_mobile_typed_many_ways_has_one_key(raw):
    assert phone_key(raw) == MOBILE_A


@pytest.mark.parametrize("raw", [None, "", "12345", "02 2345 6789", True])
def test_anything_else_is_no_phone_key(raw):
    assert phone_key(raw) is None


def test_email_keys():
    assert email_key("  Fake.Person@Example.COM ") == "fake.person@example.com"
    for raw in (None, "", "not-an-email", "a@b@c", "@example.com"):
        assert email_key(raw) is None


def test_name_keys():
    assert name_key("Fake  PERSON") == name_key("fake person") == "fake person"
    assert name_key("أحمد علي") == name_key("احمد  علي")  # alef variants
    assert name_key("مريم فاطمة") == name_key("مريم فاطمه")  # taa marbuta
    assert name_key("Fake") is None  # a first name alone never matches


HEADERS = tuple(ROSTER_COLUMNS.values())


def _roster(path: Path, rows, columns=HEADERS) -> Path:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(columns)
        writer.writerows(rows)
    return path


def test_a_roster_missing_a_column_is_refused(tmp_path):
    path = _roster(tmp_path / "roster.csv", [], columns=("Employee ID", "Full Name"))
    with pytest.raises(RosterError, match="Mobile"):
        read_roster(path)


def test_roster_rows_without_an_id_are_counted_not_used(tmp_path):
    path = _roster(tmp_path / "roster.csv", [["", "Fake Nobody", MOBILE_A, "", "Active"]])
    roster = read_roster(path)
    assert (len(roster.employees), roster.rows_without_id) == (0, 1)


COLUMNS = (*REQUIRED_COLUMNS, "Stage")
CANDIDATES = (
    # excluded by text, phone matches an active employee
    {
        "Name": "Fake Staff One",
        "Employer": "The Address Investments",
        "Phone Number": MOBILE_A,
        "Score": 0,
        "Tier": "P4",
    },
    # not excluded, email matches an active employee: missed by the text
    {
        "Name": "Fake Staff Two",
        "Employer": "Nawy",
        "Email": "fake.two@example.com",
        "Score": 70,
        "Tier": "P2",
    },
    # excluded by text, only the name matches
    {"Name": "Fake Staff Three", "Employer": "The Address", "Score": 0, "Tier": "P4"},
    # phone matches an employee who has left
    {
        "Name": "Fake Former",
        "Employer": "Nawy",
        "Phone Number": MOBILE_B,
        "Score": 60,
        "Tier": "P2",
    },
)
ROSTER = (
    ["E-1", "Fake Staff One", MOBILE_A, "", "Active"],
    ["E-2", "Different Name", "", "Fake.Two@example.com", "active"],
    ["E-3", "fake staff three", "", "", "Active"],
    ["E-4", "Fake Former", MOBILE_B, "", "Resigned"],
)


@pytest.fixture
def files(tmp_path: Path) -> tuple[Path, Path]:
    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.append(list(COLUMNS))
    for row in CANDIDATES:
        sheet.append([row.get(column) for column in COLUMNS])
    master = tmp_path / "data" / "TAI_Master.xlsx"
    master.parent.mkdir()
    workbook.save(master)
    return master, _roster(tmp_path / "data" / "roster.csv", ROSTER)


def test_matches_are_confirmed_possible_or_none(files):
    master, roster = files
    checks = {c.sheet_row: c for c in check(read_master(master), read_roster(roster))}
    assert (checks[2].excluded_by_text, checks[2].match, checks[2].employee_ids) == (
        True,
        CONFIRMED,
        ("E-1",),
    )
    assert (checks[3].excluded_by_text, checks[3].match, checks[3].matched_on) == (
        False,
        CONFIRMED,
        "email",
    )
    assert (checks[4].excluded_by_text, checks[4].match) == (True, POSSIBLE)
    assert checks[4].employer_is_exactly_the_address
    assert checks[5].match == NONE  # a former employee is not own staff


def test_the_check_reports_an_employee_the_text_missed_and_leaks_nothing(files, tmp_path):
    master, roster = files
    out = tmp_path / "out"
    report = tmp_path / "OWN_STAFF_REPORT.md"
    code = main(
        [
            "--roster",
            str(roster),
            "--master",
            str(master),
            "--out",
            str(out),
            "--report-copy",
            str(report),
        ]
    )
    assert code == 1
    content = report.read_text()
    assert "**Active employees the criteria did not exclude:** sheet row 3." in content
    assert '**Employer exactly "The Address" (OPN-03):** sheet row 4 (possible).' in content
    for value in ("Fake Staff", "fake.two", MOBILE_A, "E-1"):
        assert value not in content
    (rows,) = out.glob("*-rows.csv")
    assert stat.S_IMODE(rows.stat().st_mode) == 0o600


def test_no_roster_yet_is_a_clear_error(files, tmp_path, capsys):
    master, _ = files
    assert main(["--master", str(master), "--out", str(tmp_path / "out")]) == 2
    assert "HRIS sends the employee roster" in capsys.readouterr().err
