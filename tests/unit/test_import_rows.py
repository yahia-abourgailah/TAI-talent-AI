"""How one workbook row becomes fields, an evaluation and a raw capture. Made-up data only."""

import json
from pathlib import Path

import openpyxl
import pytest

from importer.rows import (
    IMPORT_COLUMNS,
    INFERENCE_UNKNOWN,
    NOT_RECORDED,
    PLATFORM_TEST_ROW,
    SCORE_NOT_A_WHOLE_NUMBER,
    SCORE_OUT_OF_RANGE,
    UNVERIFIED,
    missing_columns,
    prepare_row,
)
from replay.workbook import read_master

ROWS = (
    {
        "Name": "Fake Person One",
        "Age": 27,
        "Years Exp": 3.0,
        "Title": "Sales Rep",
        "Score": 72,
        "Tier": "P2",
        "Recommendation": "Good Match - Call This Week",
        "Signals (Reasons to call)": "Near New Cairo; Sales",
        "Call Priority": "Call This Week",
    },
    {"Name": "Fake Person Two", "Age": "?", "Last Active": "?", "Stage": "New"},
    {"Name": "Fake Person Three", "Score": "high", "Platform": "Test"},
    {"Name": "Fake Person Four", "Age": "؟", "Years Exp": "N/A", "Score": 12345},
)


def _save(
    path: Path,
    rows: tuple[dict[str, object], ...] = ROWS,
    columns: tuple[str, ...] = IMPORT_COLUMNS,
    title: str | None = None,
    extra_cell: str | None = None,
) -> Path:
    workbook = openpyxl.Workbook()
    if title is not None:
        workbook.properties.title = title
    sheet = workbook.active
    sheet.append(list(columns))
    for row in rows:
        sheet.append([row.get(column) for column in columns])
    if extra_cell is not None:
        sheet.cell(row=2, column=len(columns) + 1, value=extra_cell)
    workbook.save(path)
    return path


@pytest.fixture
def sheet(tmp_path: Path):
    return read_master(_save(tmp_path / "TAI_Master.xlsx"))


def _fields(prepared):
    return {field.field: field for field in prepared.fields}


def test_age_is_unverified_with_unknown_inference(sheet):
    age = _fields(prepare_row(sheet.rows[0]))["age"]
    assert (age.value, age.status, age.inference) == ("27", UNVERIFIED, INFERENCE_UNKNOWN)


def test_question_marks_and_blanks_are_not_recorded_and_hold_no_value(sheet):
    fields = _fields(prepare_row(sheet.rows[1]))
    for name in ("age", "source_last_active", "phone", "email"):
        assert (fields[name].value, fields[name].status, fields[name].inference) == (
            None,
            NOT_RECORDED,
            None,
        )


def test_other_no_value_markers_are_not_recorded(sheet):
    fields = _fields(prepare_row(sheet.rows[3]))
    assert fields["age"].status == NOT_RECORDED  # Arabic question mark
    assert fields["years_experience"].status == NOT_RECORDED  # "N/A"


def test_a_copied_fact_has_no_inference(sheet):
    title = _fields(prepare_row(sheet.rows[0]))["current_title"]
    assert (title.value, title.inference) == ("Sales Rep", None)


def test_stored_score_is_copied_with_its_original_text(sheet):
    evaluation = prepare_row(sheet.rows[0]).evaluation
    assert evaluation is not None
    assert (evaluation.score, evaluation.tier) == (72, "P2")
    assert evaluation.signals == ("Near New Cairo", "Sales")
    assert evaluation.signals_text == "Near New Cairo; Sales"
    assert (evaluation.flags, evaluation.flags_text) == (None, None)  # blank stays blank (Q-12)


def test_unreadable_score_is_unresolved_not_guessed(sheet):
    prepared = prepare_row(sheet.rows[2])
    assert prepared.evaluation is None
    assert prepared.unresolved == (SCORE_NOT_A_WHOLE_NUMBER, PLATFORM_TEST_ROW)


def test_a_score_outside_0_to_100_is_unresolved(sheet):
    prepared = prepare_row(sheet.rows[3])
    assert prepared.evaluation is None
    assert prepared.unresolved == (SCORE_OUT_OF_RANGE,)


def test_unscored_row_has_no_evaluation_and_nothing_unresolved(sheet):
    prepared = prepare_row(sheet.rows[1])
    assert (prepared.evaluation, prepared.unresolved) == (None, ())


def test_pipeline_cells_stay_in_raw_only(sheet):
    assert prepare_row(sheet.rows[1]).raw_only_filled == ("Stage",)
    assert prepare_row(sheet.rows[0]).raw_only_filled == ()


def test_raw_payload_holds_the_cells_and_nothing_about_the_file(sheet):
    first = prepare_row(sheet.rows[0])
    assert first.payload == prepare_row(sheet.rows[0]).payload
    body = json.loads(first.payload)
    assert set(body) == {"sheet_row", "cells"}
    cells = dict(body["cells"])
    assert list(cells) == list(IMPORT_COLUMNS)
    assert cells["Age"] == {"type": "int", "value": 27}
    assert cells["Email"] is None


def test_keys_name_the_row_not_the_file(sheet):
    prepared = prepare_row(sheet.rows[0], source="tai_master")
    assert prepared.source_key == "tai_master:row:2"
    assert prepared.external_id == "row:2"


def test_a_re_saved_workbook_gives_the_same_row_captures(tmp_path):
    first = read_master(_save(tmp_path / "first.xlsx", title="first save"))
    second = read_master(_save(tmp_path / "second.xlsx", title="second save"))
    assert first.sha256 != second.sha256
    assert [prepare_row(r).payload_sha256 for r in first.rows] == [
        prepare_row(r).payload_sha256 for r in second.rows
    ]


def test_missing_columns_are_named(tmp_path):
    columns = tuple(c for c in IMPORT_COLUMNS if c not in ("Last Active", "Stage"))
    partial = read_master(_save(tmp_path / "partial.xlsx", columns=columns))
    assert missing_columns(partial) == ["Last Active", "Stage"]


def test_a_cell_outside_the_named_columns_is_kept_in_raw(tmp_path):
    sheet = read_master(_save(tmp_path / "extra.xlsx", extra_cell="made-up note"))
    prepared = prepare_row(sheet.rows[0])
    extra = f"(column {len(IMPORT_COLUMNS) + 1})"
    assert prepared.unnamed_filled == (extra,)
    assert dict(json.loads(prepared.payload)["cells"])[extra] == {
        "type": "str",
        "value": "made-up note",
    }
