"""B2, B4, B5 and A2 on a fabricated workbook. Made-up data only; every test rolls back."""

import uuid
from pathlib import Path

import openpyxl
import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from importer.blobs import MemoryBlobStore
from importer.reconcile import reconcile, sample_rows
from importer.rows import IMPORT_COLUMNS, PLATFORM_TEST_ROW, SCORE_NOT_A_WHOLE_NUMBER
from importer.tai_master import ImportRefused, import_workbook
from jobs.queue import ReportableError, RunLog, claim, enqueue, find_runs, run_job
from replay.workbook import read_master

ROWS = (
    {
        "Name": "Fake Person One",
        "Age": 27,
        "Title": "Sales Rep",
        "Location": "New Cairo",
        "Years Exp": 3,
        "Profile URL": "https://example.com/in/fake-one",
        "Platform": "W",
        "Score": 72,
        "Tier": "P2",
        "Recommendation": "Good Match - Call This Week",
        "Signals (Reasons to call)": "Near New Cairo; Sales",
        "Call Priority": "Call This Week",
    },
    {
        "Name": "Fake Person Two",
        "Age": "?",
        "Title": "Cashier",
        "Profile URL": "https://example.com/in/fake-two",
        "Stage": "New",
        "HR Feedback": "made-up note",
    },
    {"Profile URL": "https://example.com/in/fake-three", "Score": "high", "Platform": "Test"},
)
ACTOR = "integration-test"


def _save(
    path: Path,
    rows: tuple[dict[str, object], ...] = ROWS,
    columns: tuple[str, ...] = IMPORT_COLUMNS,
    title: str | None = None,
) -> Path:
    workbook = openpyxl.Workbook()
    if title is not None:
        workbook.properties.title = title
    sheet = workbook.active
    sheet.append(list(columns))
    for row in rows:
        sheet.append([row.get(column) for column in columns])
    path.parent.mkdir(parents=True, exist_ok=True)
    workbook.save(path)
    return path


@pytest.fixture
def master(tmp_path: Path) -> Path:
    return _save(tmp_path / "data" / "TAI_Master.xlsx")


@pytest.fixture
def source() -> str:
    """Its own source per test, so tests never meet each other's rows or a real import."""
    return f"test-{uuid.uuid4().hex[:12]}"


def _import(conn, master: Path, source: str, blobs: MemoryBlobStore | None = None) -> RunLog:
    log = RunLog()
    sheet = read_master(master)
    import_workbook(
        conn, sheet, master.read_bytes(), blobs or MemoryBlobStore(), ACTOR, log, source
    )
    return log


def _snapshot(conn, source: str):
    return conn.execute(
        text(
            """
            SELECT c.source_key, c.capture_id, c.pipeline_state, f.field, f.value,
                   f.verification_status, f.inference, f.source_ref, f.recorded_at,
                   e.score, e.tier, e.recommendation, e.signals, e.signals_text, e.call_priority
            FROM core.candidate c
            JOIN core.candidate_field f ON f.candidate_id = c.id
            LEFT JOIN core.evaluation e ON e.candidate_id = c.id
            WHERE left(c.source_key, length(:prefix)) = :prefix
            ORDER BY c.source_key, f.field
            """
        ),
        {"prefix": f"{source}:row:"},
    ).all()


def _captures(conn, source: str) -> int:
    return int(
        conn.execute(
            text("SELECT count(*) FROM raw.capture WHERE source = :source"), {"source": source}
        ).scalar_one()
    )


def _field(conn, source: str, sheet_row: int, field: str):
    return conn.execute(
        text(
            "SELECT f.value, f.verification_status, f.inference, f.source_ref, f.recorded_by "
            "FROM core.candidate_field_current f JOIN core.candidate c ON c.id = f.candidate_id "
            "WHERE c.source_key = :key AND f.field = :field"
        ),
        {"key": f"{source}:row:{sheet_row}", "field": field},
    ).one()


def test_importing_twice_changes_nothing(app_engine, master, source):
    blobs = MemoryBlobStore()
    with app_engine.connect() as conn:
        first = _import(conn, master, source, blobs)
        before = _snapshot(conn, source)
        stored_blobs = dict(blobs.objects)
        second = _import(conn, master, source, blobs)

        assert _snapshot(conn, source) == before
        assert blobs.objects == stored_blobs
        assert _captures(conn, source) == 4  # three rows and the workbook file
    assert (first.counts["candidates_new"], first.counts["raw_captures_new"]) == (3, 3)
    assert first.counts["workbook_captures_new"] == 1
    assert (second.counts["candidates_existing"], second.counts["raw_captures_existing"]) == (3, 3)
    assert second.counts["workbook_captures_existing"] == 1
    assert not {"candidates_new", "raw_captures_new", "fields_new", "evaluations_new"} & set(
        second.counts
    )


def test_a_re_saved_workbook_writes_no_new_row_captures(app_engine, tmp_path, source):
    original = _save(tmp_path / "a" / "TAI_Master.xlsx", title="first save")
    resaved = _save(tmp_path / "b" / "TAI_Master.xlsx", title="second save")
    assert read_master(original).sha256 != read_master(resaved).sha256

    blobs = MemoryBlobStore()
    with app_engine.connect() as conn:
        _import(conn, original, source, blobs)
        before = _snapshot(conn, source)
        second = _import(conn, resaved, source, blobs)
        assert _snapshot(conn, source) == before
        assert _captures(conn, source) == 5  # three rows and two workbook files

    assert "raw_captures_new" not in second.counts
    assert second.counts["candidates_existing"] == 3
    assert second.counts["workbook_captures_new"] == 1
    assert not [u for u in second.unresolved if u["code"] == "row_differs_from_earlier_import"]


def test_a_changed_row_is_listed_and_nothing_is_written_for_it(
    app_engine, tmp_path, master, source
):
    changed = _save(
        tmp_path / "b" / "TAI_Master.xlsx",
        rows=(dict(ROWS[0], Title="Senior Sales Rep"), *ROWS[1:]),
    )
    with app_engine.connect() as conn:
        _import(conn, master, source)
        second = _import(conn, changed, source)
        assert _field(conn, source, 2, "current_title").value == "Sales Rep"
        assert _captures(conn, source) == 5  # three rows and two workbook files, nothing more

    assert second.counts["rows_differing_from_earlier_import"] == 1
    assert "raw_captures_new" not in second.counts
    assert {"code": "row_differs_from_earlier_import", "sheet_row": 2} in second.unresolved


def test_a_sheet_missing_a_column_is_refused(app_engine, tmp_path, source):
    columns = tuple(c for c in IMPORT_COLUMNS if c != "Stage")
    partial = _save(tmp_path / "p" / "TAI_Master.xlsx", columns=columns)
    with app_engine.connect() as conn, pytest.raises(ImportRefused, match="Stage"):
        _import(conn, partial, source)


def test_fields_keep_their_source_and_nothing_is_guessed(app_engine, master, source):
    with app_engine.connect() as conn:
        _import(conn, master, source)
        value, status, inference, ref, recorded_by = _field(conn, source, 2, "age")
        assert (value, status, inference, recorded_by) == ("27", "unverified", "unknown", ACTOR)
        assert ref.startswith("raw.capture:") and ref.endswith("#Age")
        assert tuple(_field(conn, source, 3, "age")[:3]) == (None, "not_recorded", None)
        assert tuple(_field(conn, source, 2, "phone")[:3]) == (None, "not_recorded", None)
        assert tuple(_field(conn, source, 2, "current_title")[:3]) == (
            "Sales Rep",
            "unverified",
            None,
        )


def test_stored_score_is_saved_as_an_evaluation_under_2026_08_04(app_engine, master, source):
    with app_engine.connect() as conn:
        log = _import(conn, master, source)
        rows = conn.execute(
            text(
                "SELECT c.source_key, e.criteria_version_id, e.origin, e.score, e.tier, "
                "e.recommendation, e.signals, e.signals_text, e.flags, e.flags_text, "
                "e.call_priority "
                "FROM core.evaluation e JOIN core.candidate c ON c.id = e.candidate_id "
                "WHERE left(c.source_key, length(:p)) = :p"
            ),
            {"p": f"{source}:row:"},
        ).all()
    assert len(rows) == 1
    (row,) = rows
    assert (row.criteria_version_id, row.origin, row.score, row.tier) == (
        "2026-08-04",
        "stored",
        72,
        "P2",
    )
    assert (row.signals, row.signals_text) == (["Near New Cairo", "Sales"], "Near New Cairo; Sales")
    assert (row.flags, row.flags_text) == (None, None)
    assert row.call_priority == "Call This Week"
    assert {"code": SCORE_NOT_A_WHOLE_NUMBER, "sheet_row": 4} in log.unresolved
    assert {"code": PLATFORM_TEST_ROW, "sheet_row": 4} in log.unresolved


def test_empty_pipeline_is_not_recorded_and_filled_pipeline_stays_in_raw(
    app_engine, master, source
):
    blobs = MemoryBlobStore()
    with app_engine.connect() as conn:
        log = _import(conn, master, source, blobs)
        states = dict(
            conn.execute(
                text(
                    "SELECT source_key, pipeline_state FROM core.candidate "
                    "WHERE left(source_key, length(:p)) = :p"
                ),
                {"p": f"{source}:row:"},
            ).all()
        )
    assert states[f"{source}:row:2"] == "not_recorded"
    assert states[f"{source}:row:3"] == "recorded_in_raw"
    assert sum(count for reason, count in log.skipped.items() if reason.startswith("Stage:")) == 1
    assert any(b"made-up note" in body for body in blobs.objects.values())


def test_an_import_run_can_be_looked_up_with_its_counts_and_skips(app_engine, master, source):
    def handler(conn, params, actor, log):
        sheet = read_master(master)
        import_workbook(conn, sheet, master.read_bytes(), MemoryBlobStore(), actor, log, source)

    with app_engine.connect() as conn:
        job_id = enqueue(conn, "test_import", {"source": source}, ACTOR)
        job = claim(conn, job_id)
        assert job is not None
        run_job(conn, job, {"test_import": handler})
        (run,) = find_runs(conn, job_id=job_id)
        status = conn.execute(
            text("SELECT status FROM jobs.job WHERE id = :id"), {"id": job_id}
        ).scalar_one()

    assert (run["outcome"], status, run["error"]) == ("succeeded", "succeeded", None)
    assert run["counts"]["rows_read"] == 3
    assert run["counts"]["candidates_new"] == 3
    assert run["counts"]["evaluations_new"] == 1
    assert len(run["input_sha256"]) == 64
    assert any(reason.startswith("HR Feedback:") for reason in run["skipped"])
    assert len(run["unresolved"]) == 2


def test_a_failed_run_is_rolled_back_and_still_recorded(app_engine, master, source):
    def handler(conn, params, actor, log):
        sheet = read_master(master)
        import_workbook(conn, sheet, master.read_bytes(), MemoryBlobStore(), actor, log, source)
        raise ReportableError("stopped on purpose")

    with app_engine.connect() as conn:
        job_id = enqueue(conn, "test_import", {}, ACTOR)
        job = claim(conn, job_id)
        assert job is not None
        run_job(conn, job, {"test_import": handler})
        (run,) = find_runs(conn, job_id=job_id)
        assert _snapshot(conn, source) == []

    assert run["outcome"] == "failed"
    assert "stopped on purpose" in run["error"]


def test_a_database_error_never_puts_row_values_in_the_run_record(app_engine):
    def handler(conn, params, actor, log):
        conn.execute(
            text(
                "INSERT INTO core.candidate_field "
                "(candidate_id, field, value, source, verification_status) "
                "VALUES (-1, 'full_name', 'Fake Secret Person', 'test', 'not_recorded')"
            )
        )

    with app_engine.connect() as conn:
        job_id = enqueue(conn, "test_import", {}, ACTOR)
        job = claim(conn, job_id)
        assert job is not None
        run_job(conn, job, {"test_import": handler})
        (run,) = find_runs(conn, job_id=job_id)

    assert run["outcome"] == "failed"
    assert "SQLSTATE" in run["error"]
    assert "Fake Secret Person" not in run["error"]


def test_the_app_cannot_change_a_recorded_run(app_engine):
    with app_engine.connect() as conn:
        job_id = enqueue(conn, "nothing", {}, ACTOR)
        job = claim(conn, job_id)
        assert job is not None
        run_id = run_job(conn, job, {"nothing": lambda *_: None})
        for statement in (
            "UPDATE audit.job_run SET error = 'edited' WHERE id = :id",
            "DELETE FROM audit.job_run WHERE id = :id",
        ):
            savepoint = conn.begin_nested()
            with pytest.raises(DBAPIError) as error:
                conn.execute(text(statement), {"id": run_id})
            savepoint.rollback()
            assert error.value.orig.sqlstate == "42501"  # type: ignore[union-attr]


def test_reconciliation_compares_the_database_with_the_sheet(app_engine, master, source):
    sheet = read_master(master)
    blobs = MemoryBlobStore()
    with app_engine.connect() as conn:
        _import(conn, master, source, blobs)
        summary = reconcile(conn, sheet, blobs, source)
        lines = sample_rows(conn, sheet, size=2, seed=1, source=source)

    assert summary["rows"] == {"sheet": 3, "candidates": 3, "match": True}
    assert summary["workbook_capture"]["match"]
    assert summary["raw_captures"]["match"]
    assert all(field["match"] for field in summary["fields"]), summary["fields"]
    age = next(f for f in summary["fields"] if f["column"] == "Age")
    assert (age["sheet_filled"], age["sheet_no_value_markers"], age["stored_values"]) == (2, 1, 1)
    assert summary["evaluations"]["match"]
    assert summary["replay"]["reads_the_same_scores_as_the_database"]
    assert summary["counts_match"]
    assert len({line["sheet_row"] for line in lines}) == 2
    assert {line["tool_agrees"] for line in lines} == {"yes"}


def test_reconciliation_notices_a_missing_candidate(app_engine, master, source):
    sheet = read_master(master)
    with app_engine.connect() as conn:
        summary = reconcile(conn, sheet, MemoryBlobStore(), source)
    assert summary["rows"]["match"] is False
    assert summary["counts_match"] is False


def test_reconciliation_notices_a_later_correction(app_engine, master, source):
    sheet = read_master(master)
    blobs = MemoryBlobStore()
    with app_engine.connect() as conn:
        _import(conn, master, source, blobs)
        candidate_id = conn.execute(
            text("SELECT id FROM core.candidate WHERE source_key = :key"),
            {"key": f"{source}:row:2"},
        ).scalar_one()
        conn.execute(
            text(
                "INSERT INTO core.candidate_field "
                "(candidate_id, field, value, source, recorded_by) "
                "VALUES (:c, 'current_title', 'Corrected Title', 'correction', :by)"
            ),
            {"c": candidate_id, "by": ACTOR},
        )
        summary = reconcile(conn, sheet, blobs, source)
    title = next(f for f in summary["fields"] if f["column"] == "Title")
    assert (title["match"], title["rows_equal_to_sheet"]) == (False, 2)
    assert summary["counts_match"] is False


def test_reconciliation_notices_a_raw_capture_that_does_not_hold_the_row(
    app_engine, master, source
):
    sheet = read_master(master)
    blobs = MemoryBlobStore()
    with app_engine.connect() as conn:
        _import(conn, master, source, blobs)
        blob_key = conn.execute(
            text(
                "SELECT r.blob_key FROM core.candidate c JOIN raw.capture r ON r.id = c.capture_id "
                "WHERE c.source_key = :key"
            ),
            {"key": f"{source}:row:2"},
        ).scalar_one()
        blobs.objects[blob_key] = b'{"sheet_row": 2, "cells": []}'
        summary = reconcile(conn, sheet, blobs, source)
    assert (summary["raw_captures"]["equal_to_sheet"], summary["raw_captures"]["match"]) == (
        2,
        False,
    )
    assert summary["counts_match"] is False
