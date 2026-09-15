"""Background jobs whose history can be looked up (B4, BR-604).

jobs.job is the queue. A worker claims the oldest queued job with FOR UPDATE SKIP LOCKED, so two
workers never run the same one. audit.job_run records every run: what it did (counts), what it
skipped and why, and anything unclear it did not resolve. It is append-only, so a run cannot be
tidied up afterwards.

A job runs inside a savepoint. If it fails, its writes are rolled back and the failed run is still
recorded; its counts then describe work that was attempted, not kept.
"""

import json
import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Connection, Engine

from db import make_engine


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


def engine_from_environment() -> Engine:
    """The app role's engine. Jobs never run as the schema owner."""
    dsn = os.environ.get("TALENT_DB_DSN")
    if not dsn:
        raise SystemExit("TALENT_DB_DSN is not set.")
    return make_engine(dsn)


def enqueue(conn: Connection, kind: str, params: Mapping[str, Any], requested_by: str) -> int:
    job_id = conn.execute(
        text(
            "INSERT INTO jobs.job (kind, params, requested_by) "
            "VALUES (:kind, CAST(:params AS jsonb), :requested_by) RETURNING id"
        ),
        {
            "kind": kind,
            "params": json.dumps(dict(params), sort_keys=True),
            "requested_by": requested_by,
        },
    ).scalar_one()
    return int(job_id)


def claim(conn: Connection, job_id: int | None = None) -> Job | None:
    """Marks the oldest queued job, or the one given, as running. None when nothing is queued."""
    only = "" if job_id is None else "AND id = :job_id"
    params: dict[str, Any] = {} if job_id is None else {"job_id": job_id}
    row = conn.execute(
        text(
            f"""
            UPDATE jobs.job SET status = 'running', started_at = clock_timestamp()
            WHERE id = (
              SELECT id FROM jobs.job WHERE status = 'queued' {only}
              ORDER BY id LIMIT 1 FOR UPDATE SKIP LOCKED
            )
            RETURNING id, kind, params, requested_by, started_at
            """
        ),
        params,
    ).one_or_none()
    if row is None:
        return None
    return Job(
        id=row.id,
        kind=row.kind,
        params=dict(row.params),
        requested_by=row.requested_by,
        started_at=row.started_at,
    )


def run_job(conn: Connection, job: Job, handlers: Mapping[str, Handler]) -> int:
    """Runs a claimed job, records the run, and returns its audit.job_run id."""
    log = RunLog()
    error: str | None = None
    savepoint = conn.begin_nested()
    try:
        handler = handlers.get(job.kind)
        if handler is None:
            raise LookupError(f"no handler for job kind {job.kind!r}")
        handler(conn, job.params, job.requested_by, log)
    except Exception as exc:
        savepoint.rollback()
        error = f"{type(exc).__name__}: {exc}"[:2000]
    else:
        savepoint.commit()

    outcome = "succeeded" if error is None else "failed"
    conn.execute(
        text(
            "UPDATE jobs.job SET status = :outcome, finished_at = clock_timestamp() WHERE id = :id"
        ),
        {"outcome": outcome, "id": job.id},
    )
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
            "job_id": job.id,
            "kind": job.kind,
            "outcome": outcome,
            "started_at": job.started_at,
            "run_by": job.requested_by,
            "input_sha256": log.input_sha256,
            "counts": json.dumps(log.counts, sort_keys=True),
            "skipped": json.dumps(log.skipped, sort_keys=True, ensure_ascii=False),
            "unresolved": json.dumps(log.unresolved, sort_keys=True, ensure_ascii=False),
            "error": error,
        },
    ).scalar_one()
    return int(run_id)


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
