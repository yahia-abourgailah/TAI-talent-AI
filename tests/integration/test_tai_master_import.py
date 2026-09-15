"""B2, B4, B5 and A2 on a fabricated workbook. Made-up data only; every test rolls back."""

import uuid
from pathlib import Path

import openpyxl
import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from importer.blobs import MemoryBlobStore
from importer.reconcile import reconcile, sample_rows
from importer.rows import PLATFORM_TEST_ROW, SCORE_NOT_A_WHOLE_NUMBER
from importer.tai_master import import_workbook
from jobs.queue import RunLog, claim, enqueue, find_runs, run_job
from replay.workbook import REQUIRED_COLUMNS, read_master

COLUMNS = (*REQUIRED_COLUMNS, "Last Active", "Call Priority", "Stage", "HR Feedback")
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


@pytest.fixture
def master(tmp_path: Path) -> Path:
    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.append(list(COLUMNS))
    for row in ROWS:
        sheet.append([row.get(column) for column in COLUMNS])
    path = tmp_path / "data" / "TAI_Master.xlsx"
    path.parent.mkdir()
    workbook.save(path)
    return path


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
                   f.verification_status, f.inference, f.source_ref,
                   e.score, e.tier, e.recommendation, e.signals, e.call_priority
            FROM core.candidate c
            JOIN core.candidate_field f ON f.candidate_id = c.id
            LEFT JOIN core.evaluation e ON e.candidate_id = c.id
            WHERE left(c.source_key, length(:prefix)) = :prefix
            ORDER BY c.source_key, f.field
            """
        ),
        {"prefix": f"{source}:row:"},
    ).all()


def _field(conn, source: str, sheet_row: int, field: str):
    return conn.execute(
        text(
            "SELECT f.value, f.verification_status, f.inference, f.source_ref "
            "FROM core.candidate_field f JOIN core.candidate c ON c.id = f.candidate_id "
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
        assert (first.counts["candidates_new"], first.counts["raw_captures_new"]) == (3, 3)
        assert (second.counts["candidates_existing"], second.counts["raw_captures_existing"]) == (
            3,
            3,
        )
        assert "candidates_new" not in second.counts
        assert "fields_new" not in second.counts
        assert "evaluations_new" not in second.counts
        count = conn.execute(
            text("SELECT count(*) FROM core.candidate WHERE left(source_key, length(:p)) = :p"),
            {"p": f"{source}:row:"},
        ).scalar_one()
        assert count == 3


def test_fields_keep_their_source_and_nothing_is_guessed(app_engine, master, source):
    with app_engine.connect() as conn:
        _import(conn, master, source)
        value, status, inference, ref = _field(conn, source, 2, "age")
        assert (value, status, inference) == ("27", "unverified", "unknown")
        assert ref.startswith("raw.capture:") and ref.endswith("#Age")
        assert _field(conn, source, 3, "age")[:3] == (None, "not_recorded", None)
        assert _field(conn, source, 2, "phone")[:3] == (None, "not_recorded", None)
        assert _field(conn, source, 2, "current_title")[:3] == ("Sales Rep", "unverified", None)


def test_stored_score_is_saved_as_an_evaluation_under_2026_08_04(app_engine, master, source):
    with app_engine.connect() as conn:
        log = _import(conn, master, source)
        rows = conn.execute(
            text(
                "SELECT c.source_key, e.criteria_version_id, e.origin, e.score, e.tier, "
                "e.recommendation, e.signals, e.flags, e.call_priority "
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
    assert row.signals == ["Near New Cairo", "Sales"]
    assert row.flags is None
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
        raise RuntimeError("stopped on purpose")

    with app_engine.connect() as conn:
        job_id = enqueue(conn, "test_import", {}, ACTOR)
        job = claim(conn, job_id)
        assert job is not None
        run_job(conn, job, {"test_import": handler})
        (run,) = find_runs(conn, job_id=job_id)
        assert _snapshot(conn, source) == []

    assert run["outcome"] == "failed"
    assert "stopped on purpose" in run["error"]


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


def test_reconciliation_counts_match_and_the_sample_agrees(app_engine, master, source):
    sheet = read_master(master)
    with app_engine.connect() as conn:
        _import(conn, master, source)
        summary = reconcile(conn, sheet, source)
        lines = sample_rows(conn, sheet, size=2, seed=1, source=source)

    assert summary["rows"] == {"sheet": 3, "candidates": 3, "match": True}
    assert summary["raw_captures"]["match"]
    assert all(field["match"] for field in summary["fields"]), summary["fields"]
    age = next(f for f in summary["fields"] if f["column"] == "Age")
    assert (age["sheet_filled"], age["sheet_question_marks"], age["stored_values"]) == (2, 1, 1)
    assert summary["evaluations"]["match"]
    assert summary["replay"]["reads_the_same_stored_scores"]
    assert summary["counts_match"]
    assert len({line["sheet_row"] for line in lines}) == 2
    assert {line["agrees"] for line in lines} == {"yes"}


def test_reconciliation_notices_a_missing_candidate(app_engine, master, source):
    sheet = read_master(master)
    with app_engine.connect() as conn:
        summary = reconcile(conn, sheet, source)
    assert summary["rows"]["match"] is False
    assert summary["counts_match"] is False
