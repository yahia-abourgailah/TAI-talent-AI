"""Background jobs whose history can be looked up (B4, BR-604, NFR-04).

jobs.job is the queue. A worker claims the oldest queued job with FOR UPDATE SKIP LOCKED, so two
workers never run the same one, marks it running in its own committed transaction, and holds a
Postgres advisory lock on the job for as long as it works. The work runs in a second transaction.

audit.job_run records every run: what it did (counts), what it skipped and why, and anything
unclear it did not resolve. It is append-only, so a run cannot be tidied up afterwards.

If a worker stops mid-run (killed, crashed, connection lost), its work transaction rolls back, so
nothing half-done is kept, and its advisory lock disappears with its connection. recover_stopped
then finds the job still marked running with no lock, records a failed run saying the worker
stopped, and queues the job again, until MAX_ATTEMPTS.

A failed run records what kind of failure it was, never the exception's message, unless the
failure is a ReportableError. Database errors quote the values of the row that failed, which can
be a candidate's name, phone or email, and a run record is kept forever.
"""

import json
import os
from collections.abc import Callable, Mapping, Sequence
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.exc import DBAPIError

from db import make_engine

MAX_ATTEMPTS = 3
WORKER_STOPPED = "The worker stopped before the job finished. Nothing from that attempt was kept."
_LOCK_NAMESPACE = 7301


class ReportableError(Exception):
    """A failure whose message is safe to keep: it names files, columns or rows, never people."""


@dataclass
class RunLog:
    """What a job did. Skips and unresolved items name sheet rows or columns, never people."""

    input_sha256: str | None = None
    counts: dict[str, int] = field(default_factory=dict)
    skipped: dict[str, int] = field(default_factory=dict)
    unresolved: list[dict[str, Any]] = field(default_factory=list)

    def count(self, name: str, by: int = 1) -> None:
        self.counts[name] = self.counts.get(name, 0) + by

    def skip(self, reason: str, by: int = 1) -> None:
        self.skipped[reason] = self.skipped.get(reason, 0) + by

    def unresolve(self, code: str, **detail: Any) -> None:
        self.unresolved.append({"code": code, **detail})


Handler = Callable[[Connection, Mapping[str, Any], str, RunLog], None]


@dataclass(frozen=True, slots=True)
class Job:
    id: int
    kind: str
    params: dict[str, Any]
    requested_by: str
    started_at: datetime
    attempts: int


def describe_failure(exc: BaseException) -> str:
    """What failed, without the exception's message unless it is known to hold no personal data."""
    if isinstance(exc, ReportableError):
        return f"{type(exc).__name__}: {exc}"[:2000]
    sqlstate = getattr(getattr(exc, "orig", None), "sqlstate", None)
    if isinstance(sqlstate, str) and sqlstate:
        return f"{type(exc).__name__} (SQLSTATE {sqlstate})"
    return type(exc).__name__


def lock_key(job_id: int) -> int:
    """The advisory lock a worker holds while it works on a job."""
    return _LOCK_NAMESPACE * 2**32 + job_id


def _try_lock(conn: Connection, job_id: int) -> bool:
    return bool(
        conn.execute(
            text("SELECT pg_try_advisory_lock(CAST(:key AS bigint))"), {"key": lock_key(job_id)}
        ).scalar_one()
    )


def _unlock(conn: Connection, job_id: int) -> None:
    conn.execute(text("SELECT pg_advisory_unlock(CAST(:key AS bigint))"), {"key": lock_key(job_id)})


def engine_from_environment() -> Engine:
    """The app role's engine. Jobs never run as the schema owner."""
    dsn = os.environ.get("TALENT_DB_DSN")
    if not dsn:
        raise SystemExit("TALENT_DB_DSN is not set.")
    return make_engine(dsn)


def _params_json(params: Mapping[str, Any]) -> str:
    return json.dumps(dict(params), sort_keys=True)


def enqueue(conn: Connection, kind: str, params: Mapping[str, Any], requested_by: str) -> int:
    job_id = conn.execute(
        text(
            "INSERT INTO jobs.job (kind, params, requested_by) "
            "VALUES (:kind, CAST(:params AS jsonb), :requested_by) RETURNING id"
        ),
        {"kind": kind, "params": _params_json(params), "requested_by": requested_by},
    ).scalar_one()
    return int(job_id)


def find_queued(conn: Connection, kind: str, params: Mapping[str, Any]) -> int | None:
    """The oldest queued job of this kind with exactly these params, if there is one."""
    found = conn.execute(
        text(
            "SELECT id FROM jobs.job WHERE status = 'queued' AND kind = :kind "
            "AND params = CAST(:params AS jsonb) ORDER BY id LIMIT 1"
        ),
        {"kind": kind, "params": _params_json(params)},
    ).scalar_one_or_none()
    return None if found is None else int(found)


def claim(
    conn: Connection,
    job_id: int | None = None,
    *,
    lock_conn: Connection | None = None,
    kinds: Sequence[str] | None = None,
) -> Job | None:
    """Marks the oldest queued job, or the one given, as running. None when nothing is queued.

    With kinds, only a job of one of those kinds is claimed: a worker never takes a job it has no
    handler for, which would fail a job another worker could run.

    With lock_conn, the job's advisory lock is taken on that connection first, and held until the
    caller releases it. Without it the job is not protected from recover_stopped; that is only for
    a claim and run inside one transaction, as the tests do.
    """
    only = "" if job_id is None else "AND id = :job_id"
    params: dict[str, Any] = {"kinds": None if kinds is None else list(kinds)}
    if job_id is not None:
        params["job_id"] = job_id
    found = conn.execute(
        text(
            f"SELECT id FROM jobs.job WHERE status = 'queued' {only} "
            "AND (CAST(:kinds AS text[]) IS NULL OR kind = ANY(:kinds)) "
            "ORDER BY id LIMIT 1 FOR UPDATE SKIP LOCKED"
        ),
        params,
    ).scalar_one_or_none()
    if found is None:
        return None
    if lock_conn is not None:
        if not _try_lock(lock_conn, int(found)):
            return None
        lock_conn.commit()  # the lock belongs to the session and outlives this transaction
    row = conn.execute(
        text(
            """
            UPDATE jobs.job
            SET status = 'running', started_at = clock_timestamp(), attempts = attempts + 1
            WHERE id = :id
            RETURNING id, kind, params, requested_by, started_at, attempts
            """
        ),
        {"id": found},
    ).one()
    return Job(
        id=row.id,
        kind=row.kind,
        params=dict(row.params),
        requested_by=row.requested_by,
        started_at=row.started_at,
        attempts=row.attempts,
    )


def _record_run(
    conn: Connection,
    *,
    job_id: int,
    kind: str,
    outcome: str,
    started_at: datetime,
    run_by: str,
    log: RunLog,
    error: str | None,
) -> int:
    run_id = conn.execute(
        text(
            """
            INSERT INTO audit.job_run
              (job_id, kind, outcome, started_at, finished_at, run_by, input_sha256,
               counts, skipped, unresolved, error)
            VALUES
              (:job_id, :kind, :outcome, :started_at, clock_timestamp(), :run_by, :input_sha256,
               CAST(:counts AS jsonb), CAST(:skipped AS jsonb), CAST(:unresolved AS jsonb), :error)
            RETURNING id
            """
        ),
        {
            "job_id": job_id,
            "kind": kind,
            "outcome": outcome,
            "started_at": started_at,
            "run_by": run_by,
            "input_sha256": log.input_sha256,
            "counts": json.dumps(log.counts, sort_keys=True),
            "skipped": json.dumps(log.skipped, sort_keys=True, ensure_ascii=False),
            "unresolved": json.dumps(log.unresolved, sort_keys=True, ensure_ascii=False),
            "error": error,
        },
    ).scalar_one()
    return int(run_id)


def run_job(conn: Connection, job: Job, handlers: Mapping[str, Handler]) -> int:
    """Runs a claimed job, records the run, and returns its audit.job_run id.

    The job runs inside a savepoint. If it fails, its writes are rolled back and the failed run is
    still recorded; its counts then describe work that was attempted, not kept.
    """
    log = RunLog()
    error: str | None = None
    savepoint = conn.begin_nested()
    try:
        handler = handlers.get(job.kind)
        if handler is None:
            raise ReportableError(f"no handler for job kind {job.kind!r}")
        handler(conn, job.params, job.requested_by, log)
    except Exception as exc:
        savepoint.rollback()
        error = describe_failure(exc)
    else:
        savepoint.commit()

    outcome = "succeeded" if error is None else "failed"
    conn.execute(
        text(
            "UPDATE jobs.job SET status = :outcome, finished_at = clock_timestamp() WHERE id = :id"
        ),
        {"outcome": outcome, "id": job.id},
    )
    return _record_run(
        conn,
        job_id=job.id,
        kind=job.kind,
        outcome=outcome,
        started_at=job.started_at,
        run_by=job.requested_by,
        log=log,
        error=error,
    )


def work_one(
    engine: Engine, handlers: Mapping[str, Handler], job_id: int | None = None
) -> tuple[Job, dict[str, Any]] | None:
    """Claims one job, runs it and records the run. None when there is nothing to claim."""
    with engine.connect() as lock_conn:
        try:
            with engine.begin() as conn:
                job = claim(conn, job_id, lock_conn=lock_conn, kinds=list(handlers))
            if job is None:
                return None
            with engine.begin() as conn:
                run_id = run_job(conn, job, handlers)
                (run,) = [r for r in find_runs(conn, job_id=job.id, limit=1) if r["id"] == run_id]
            return job, run
        finally:
            with suppress(DBAPIError):
                lock_conn.rollback()
                lock_conn.execute(text("SELECT pg_advisory_unlock_all()"))
                lock_conn.commit()


def recover_stopped(engine: Engine, kind: str | None = None) -> list[tuple[int, str]]:
    """Finds jobs still marked running whose worker has gone, records a failed run for each, and
    queues it again, or fails it once it has had MAX_ATTEMPTS. Returns (job id, new status).

    A worker that is still working holds its job's advisory lock, so its job is left alone.
    """
    recovered: list[tuple[int, str]] = []
    with engine.connect() as lock_conn, engine.begin() as conn:
        running = conn.execute(
            text(
                """
                SELECT id, kind, requested_by, started_at, attempts FROM jobs.job
                WHERE status = 'running' AND (CAST(:kind AS text) IS NULL OR kind = :kind)
                ORDER BY id FOR UPDATE SKIP LOCKED
                """
            ),
            {"kind": kind},
        ).all()
        for job in running:
            if not _try_lock(lock_conn, job.id):
                continue
            try:
                status = "failed" if job.attempts >= MAX_ATTEMPTS else "queued"
                conn.execute(
                    text(
                        "UPDATE jobs.job SET status = CAST(:status AS text), finished_at = "
                        "CASE WHEN CAST(:status AS text) = 'failed' THEN clock_timestamp() END "
                        "WHERE id = :id"
                    ),
                    {"status": status, "id": job.id},
                )
                _record_run(
                    conn,
                    job_id=job.id,
                    kind=job.kind,
                    outcome="failed",
                    started_at=job.started_at,
                    run_by=job.requested_by,
                    log=RunLog(),
                    error=WORKER_STOPPED,
                )
                recovered.append((int(job.id), status))
            finally:
                _unlock(lock_conn, job.id)
        lock_conn.commit()
    return recovered


def find_runs(
    conn: Connection, *, job_id: int | None = None, kind: str | None = None, limit: int = 20
) -> list[dict[str, Any]]:
    """Recorded runs, newest first."""
    rows = conn.execute(
        text(
            """
            SELECT id, job_id, kind, outcome, started_at, finished_at, run_by, input_sha256,
                   counts, skipped, unresolved, error
            FROM audit.job_run
            WHERE (CAST(:job_id AS bigint) IS NULL OR job_id = :job_id)
              AND (CAST(:kind AS text) IS NULL OR kind = :kind)
            ORDER BY id DESC
            LIMIT :limit
            """
        ),
        {"job_id": job_id, "kind": kind, "limit": limit},
    ).mappings()
    return [dict(row) for row in rows]
