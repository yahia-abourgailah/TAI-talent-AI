"""BR-704: rows the 3 August fixes could have changed, on a fabricated workbook."""

from datetime import date
from pathlib import Path

import openpyxl
import pytest

from replay.baseline import run_baseline
from replay.fixes import caught_by_old_country_match, caught_by_old_manager_match
from replay.rescore import ALREADY, PENDING_TIER, check, main, render_report
from replay.workbook import REQUIRED_COLUMNS, read_master

RUN_DATE = date(2026, 9, 14)
COLUMNS = (*REQUIRED_COLUMNS, "Stage")


@pytest.mark.parametrize(
    ("location", "caught"),
    [
        ("El Shorouk City", True),  # "uk" inside Shorouk
        ("London, UK", False),  # a real country: disqualified before and after
        ("New Cairo", False),
        ("", False),
    ],
)
def test_country_fix(location, caught):
    assert caught_by_old_country_match(location) is caught


@pytest.mark.parametrize(
    ("title", "caught"),
    [
        ("Sales Coordinator", True),  # "coo" inside Coordinator
        ("Sales Managerat Nawy", True),  # a manager word glued to the next word
        ("COO", False),
        ("Senior Sales Manager", False),
        ("Sales Rep", False),
    ],
)
def test_manager_fix(title, caught):
    assert caught_by_old_manager_match(title) is caught


ROWS = (
    {
        "Name": "Fake Person One",
        "Location": "El Shorouk City",
        "Title": "Sales Rep",
        "Date Added": "2026-07-01",
    },
    {
        "Name": "Fake Person Two",
        "Location": "Maadi",
        "Title": "Sales Coordinator",
        "Date Added": "2026-07-02",
    },
    {
        "Name": "Fake Person Three",
        "Location": "Nasr City",
        "Title": "Cashier",
        "Date Added": "2026-08-20",
    },
)


def _save(path: Path, rows) -> Path:
    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.append(list(COLUMNS))
    for row in rows:
        sheet.append([row.get(column) for column in COLUMNS])
    workbook.save(path)
    return path


@pytest.fixture
def master(tmp_path: Path) -> Path:
    unscored = read_master(_save(tmp_path / "unscored.xlsx", ROWS))
    today = {r.sheet_row: r.replayed for r in run_baseline(unscored, RUN_DATE)}
    stored = [
        dict(ROWS[0], Score=today[2].score, Tier=today[2].tier),  # already re-scored
        dict(ROWS[1], Score=0, Tier="P4"),  # still the old disqualification
        dict(ROWS[2], Score=today[4].score, Tier=today[4].tier),
    ]
    path = tmp_path / "data" / "TAI_Master.xlsx"
    path.parent.mkdir()
    return _save(path, stored)


def test_rows_the_fixes_could_change_are_classified(master):
    summary = check(read_master(master), RUN_DATE)
    assert summary["per_fix"]["country"]["rows"] == 1
    assert summary["per_fix"]["country"][ALREADY] == 1
    assert summary["per_fix"]["manager"]["rows"] == 1
    assert summary["per_fix"]["manager"][PENDING_TIER] == 1
    assert [c.sheet_row for c in summary["pending"]] == [3]
    assert summary["added_before_fix"][ALREADY] == 1
    assert summary["added_before_fix"][PENDING_TIER] == 1


def test_a_pending_change_fails_and_the_report_holds_no_candidate_values(master, tmp_path):
    report = tmp_path / "RESCORE_REPORT.md"
    assert (
        main(
            [
                "--master",
                str(master),
                "--run-date",
                RUN_DATE.isoformat(),
                "--report-copy",
                str(report),
            ]
        )
        == 1
    )
    content = report.read_text()
    assert "**Pending changes: 1.**" in content
    assert "| 3 | manager | 0 P4 |" in content
    for row in ROWS:
        assert row["Name"] not in content
        assert row["Title"] not in content


def test_nothing_pending_passes(master):
    summary = check(read_master(master), RUN_DATE)
    summary["pending"] = []
    assert "**Pending changes: 0.**" in render_report(summary)
