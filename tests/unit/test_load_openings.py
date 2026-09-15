"""A2 without the API: a jobs file is checked line by line, and one bad line refuses the file."""

from pathlib import Path

import pytest

from pipeline.load_openings import COLUMNS, LoadRefused, check_lines, read_rows

HEADER = ",".join(COLUMNS)
GOOD = "Made-up Brand,Sales,A,3,dev|recruiter-a,team-a,2026-08-04"


def _csv(tmp_path: Path, *lines: str) -> Path:
    path = tmp_path / "jobs.csv"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def test_valid_lines_are_read_with_their_line_numbers(tmp_path):
    other = "Made-up Brand,Leasing,b,1,dev|recruiter-b,team-b,2026-08-04"
    (first, second) = check_lines(read_rows(_csv(tmp_path, HEADER, GOOD, "", other)))
    assert (first.line, first.headcount, first.track) == (2, 3, "A")
    assert (second.line, second.track) == (4, "B")  # blank line 3 skipped; track upper-cased
    assert second.body()["criteria_version"] == "2026-08-04"


def test_every_problem_is_reported_by_line_and_nothing_is_accepted(tmp_path):
    bad = "Made-up Brand,,C,two,dev|recruiter-b,team-b,2026-08-04"
    with pytest.raises(LoadRefused) as refused:
        check_lines(read_rows(_csv(tmp_path, HEADER, GOOD, bad, GOOD)))
    assert refused.value.problems == [
        "line 3: department is missing",
        "line 3: track must be A or B",
        "line 3: headcount must be a whole number from 1 to 10000",
        "line 4: the same job as line 2",
    ]


@pytest.mark.parametrize("headcount", ["0", "-2", "1.5", "10001"])
def test_a_headcount_outside_1_to_10000_is_refused(tmp_path, headcount):
    line = f"Made-up Brand,Sales,A,{headcount},dev|recruiter-a,team-a,2026-08-04"
    with pytest.raises(LoadRefused, match="line 2: headcount"):
        check_lines(read_rows(_csv(tmp_path, HEADER, line)))


def test_a_file_missing_a_column_is_refused(tmp_path):
    header = ",".join(c for c in COLUMNS if c != "team")
    with pytest.raises(LoadRefused, match="missing columns team"):
        read_rows(_csv(tmp_path, header, "Made-up Brand,Sales,A,3,dev|recruiter-a,2026-08-04"))


def test_header_order_and_case_do_not_matter(tmp_path):
    header = "TEAM,Brand,Department,Track,Headcount,Owner_Recruiter,Criteria_Version"
    (line,) = check_lines(
        read_rows(
            _csv(tmp_path, header, "team-a,Made-up Brand,Sales,A,3,dev|recruiter-a,2026-08-04")
        )
    )
    assert (line.team, line.brand) == ("team-a", "Made-up Brand")


def test_a_file_with_no_job_lines_is_refused(tmp_path):
    with pytest.raises(LoadRefused, match="no job lines"):
        check_lines(read_rows(_csv(tmp_path, HEADER)))
