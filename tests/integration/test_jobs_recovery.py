"""NFR-04 and BR-604: a worker that stops mid-run leaves a record, and its job runs again.

These tests commit, because a stopped worker's job is seen from other connections. Each test uses
its own job kind, so tests never touch each other's jobs. Made-up data only.
"""

import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from jobs.queue import (
    MAX_ATTEMPTS,
    WORKER_STOPPED,
    enqueue,
    find_runs,
    lock_key,
    recover_stopped,
    work_one,
)

ACTOR = "integration-test"


@pytest.fixture
def kind() -> str:
    return f"test-recovery-{uuid.uuid4().hex[:12]}"


def _left_running(engine, kind: str, attempts: int = 1) -> int:
    """What a killed worker leaves behind: a job still marked running, and no lock."""
    with engine.begin() as conn:
        job_id = enqueue(conn, kind, {}, ACTOR)
        conn.execute(
            text(
                "UPDATE jobs.job SET status = 'running', started_at = clock_timestamp(), "
                "attempts = :attempts WHERE id = :id"
            ),
            {"attempts": attempts, "id": job_id},
        )
    return job_id


def _state(engine, job_id: int):
    with engine.connect() as conn:
        return conn.execute(
            text("SELECT status, attempts, finished_at FROM jobs.job WHERE id = :id"),
            {"id": job_id},
        ).one()


def test_a_stopped_job_is_recorded_and_queued_again(app_engine, kind):
    job_id = _left_running(app_engine, kind)
    assert recover_stopped(app_engine, kind) == [(job_id, "queued")]
    with app_engine.connect() as conn:
        (run,) = find_runs(conn, job_id=job_id)
    assert (run["outcome"], run["error"]) == ("failed", WORKER_STOPPED)
    assert _state(app_engine, job_id).status == "queued"


def test_a_recovered_job_then_runs_to_completion(app_engine, kind):
    job_id = _left_running(app_engine, kind)
    recover_stopped(app_engine, kind)
    result = work_one(app_engine, {kind: lambda *_: None}, job_id)
    assert result is not None
    job, run = result
    assert (job.attempts, run["outcome"]) == (2, "succeeded")
    assert _state(app_engine, job_id).status == "succeeded"
    with app_engine.connect() as conn:
        assert [r["outcome"] for r in find_runs(conn, job_id=job_id)] == ["succeeded", "failed"]


def test_a_job_that_keeps_stopping_is_failed(app_engine, kind):
    job_id = _left_running(app_engine, kind, attempts=MAX_ATTEMPTS)
    assert recover_stopped(app_engine, kind) == [(job_id, "failed")]
    state = _state(app_engine, job_id)
    assert (state.status, state.finished_at is not None) == ("failed", True)


def test_a_job_whose_worker_is_still_working_is_left_alone(app_engine, kind):
    job_id = _left_running(app_engine, kind)
    with app_engine.connect() as worker:
        worker.execute(
            text("SELECT pg_advisory_lock(CAST(:key AS bigint))"), {"key": lock_key(job_id)}
        )
        worker.commit()
        try:
            assert recover_stopped(app_engine, kind) == []
            assert _state(app_engine, job_id).status == "running"
        finally:
            worker.execute(
                text("SELECT pg_advisory_unlock(CAST(:key AS bigint))"), {"key": lock_key(job_id)}
            )
            worker.commit()


@pytest.mark.parametrize(
    ("statement", "refusal"),
    [
        ("UPDATE jobs.job SET requested_by = 'someone else' WHERE id = :id", "never change"),
        ("UPDATE jobs.job SET status = 'queued' WHERE id = :id", "final"),
        ("DELETE FROM jobs.job WHERE id = :id", "permission denied"),
    ],
)
def test_a_finished_job_cannot_be_edited_or_deleted(app_engine, kind, statement, refusal):
    with app_engine.begin() as conn:
        job_id = enqueue(conn, kind, {}, ACTOR)
    assert work_one(app_engine, {kind: lambda *_: None}, job_id) is not None
    with app_engine.connect() as conn, pytest.raises(DBAPIError) as error:
        conn.execute(text(statement), {"id": job_id})
    assert refusal in str(error.value.orig)


def test_a_finished_job_is_not_claimed_again(app_engine, kind):
    with app_engine.begin() as conn:
        job_id = enqueue(conn, kind, {}, ACTOR)
    assert work_one(app_engine, {kind: lambda *_: None}, job_id) is not None
    assert work_one(app_engine, {kind: lambda *_: None}, job_id) is None
