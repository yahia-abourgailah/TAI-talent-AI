"""How one workbook row becomes fields, an evaluation and a raw capture. Made-up data only."""

import json
from pathlib import Path

import openpyxl
import pytest

from importer.rows import (
    INFERENCE_UNKNOWN,
    NOT_RECORDED,
    PLATFORM_TEST_ROW,
    SCORE_NOT_A_WHOLE_NUMBER,
    UNVERIFIED,
    prepare_row,
)
from replay.workbook import REQUIRED_COLUMNS, read_master

COLUMNS = (*REQUIRED_COLUMNS, "Last Active", "Call Priority", "Stage", "HR Feedback")


@pytest.fixture
def sheet(tmp_path: Path):
    workbook = openpyxl.Workbook()
    rows = [
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
    ]
    sheet = workbook.active
    sheet.append(list(COLUMNS))
    for row in rows:
        sheet.append([row.get(column) for column in COLUMNS])
    path = tmp_path / "TAI_Master.xlsx"
    workbook.save(path)
    return read_master(path)


def _fields(prepared):
    return {field.field: field for field in prepared.fields}


def test_age_is_unverified_with_unknown_inference(sheet):
    age = _fields(prepare_row(sheet, sheet.rows[0]))["age"]
    assert (age.value, age.status, age.inference) == ("27", UNVERIFIED, INFERENCE_UNKNOWN)


def test_question_marks_and_blanks_are_not_recorded_and_hold_no_value(sheet):
    fields = _fields(prepare_row(sheet, sheet.rows[1]))
    for name in ("age", "source_last_active", "phone", "email"):
        assert (fields[name].value, fields[name].status, fields[name].inference) == (
            None,
            NOT_RECORDED,
            None,
        )


def test_a_copied_fact_has_no_inference(sheet):
    title = _fields(prepare_row(sheet, sheet.rows[0]))["current_title"]
    assert (title.value, title.inference) == ("Sales Rep", None)


def test_stored_score_is_copied_not_recomputed(sheet):
    evaluation = prepare_row(sheet, sheet.rows[0]).evaluation
    assert evaluation is not None
    assert (evaluation.score, evaluation.tier) == (72, "P2")
    assert evaluation.signals == ("Near New Cairo", "Sales")
    assert evaluation.flags is None  # blank stays blank until Q-12


def test_unreadable_score_is_unresolved_not_guessed(sheet):
    prepared = prepare_row(sheet, sheet.rows[2])
    assert prepared.evaluation is None
    assert prepared.unresolved == (SCORE_NOT_A_WHOLE_NUMBER, PLATFORM_TEST_ROW)


def test_unscored_row_has_no_evaluation_and_nothing_unresolved(sheet):
    prepared = prepare_row(sheet, sheet.rows[1])
    assert (prepared.evaluation, prepared.unresolved) == (None, ())


def test_pipeline_cells_stay_in_raw_only(sheet):
    assert prepare_row(sheet, sheet.rows[1]).raw_only_filled == ("Stage",)
    assert prepare_row(sheet, sheet.rows[0]).raw_only_filled == ()


def test_raw_payload_keeps_every_cell_typed_and_is_stable(sheet):
    first = prepare_row(sheet, sheet.rows[0])
    again = prepare_row(sheet, sheet.rows[0])
    assert first.payload == again.payload
    cells = dict(json.loads(first.payload)["cells"])
    assert list(cells) == list(COLUMNS)
    assert cells["Age"] == {"type": "int", "value": 27}
    assert cells["Email"] is None


def test_keys_name_the_row_and_the_workbook(sheet):
    prepared = prepare_row(sheet, sheet.rows[0], source="tai_master")
    assert prepared.source_key == "tai_master:row:2"
    assert prepared.external_id == f"{sheet.sha256}:2"
