"""The watch reads the real queue, and only an admin may ask the API for it (NFR-05)."""

import pytest
from sqlalchemy import text

from ops.watch import CRITICAL, OK, jobs_stuck, queue_waiting, run_checks

from .conftest import sign_in

KIND = "test-watch-stuck"


@pytest.fixture
def conn(app_engine):
    """Nothing here is committed: the connection closes without one, as everywhere else."""
    with app_engine.connect() as connection:
        yield connection


def test_a_quiet_queue_reads_as_ok(conn):
    assert queue_waiting(conn).status == OK
    assert jobs_stuck(conn).status == OK


def test_a_job_running_since_yesterday_is_critical(conn):
    conn.execute(
        text(
            "INSERT INTO jobs.job (kind, params, status, requested_by, requested_at, started_at) "
            "VALUES (:kind, '{}', 'running', 'integration-test', now() - interval '2 days', "
            "        now() - interval '2 days')"
        ),
        {"kind": "score_application"},  # a real kind: test-… kinds are left out on purpose
    )
    check = jobs_stuck(conn)
    assert check.status == CRITICAL
    assert check.numbers["stuck"] == 1
    assert check.numbers["oldest_minutes"] > 60


def test_test_jobs_are_not_mistaken_for_a_broken_platform(conn):
    conn.execute(
        text(
            "INSERT INTO jobs.job (kind, params, status, requested_by, requested_at, started_at) "
            "VALUES (:kind, '{}', 'running', 'integration-test', now() - interval '2 days', "
            "        now() - interval '2 days')"
        ),
        {"kind": "test-watch-left-behind"},
    )
    assert jobs_stuck(conn).status == OK


def test_every_check_runs_against_the_real_schema(conn):
    names = [check.name for check in run_checks(conn, None)]
    assert names == [
        "queue_waiting",
        "jobs_failing",
        "jobs_stuck",
        "events_undelivered",
        "backup",
    ]


def test_only_an_admin_reads_the_platforms_health(api_client):
    client, _connection = api_client
    assert client.get("/v1/ops/health", headers=sign_in(client, "ta-lead")).status_code == 403
    assert client.get("/v1/ops/health", headers=sign_in(client, "recruiter-a")).status_code == 403

    answer = client.get("/v1/ops/health", headers=sign_in(client, "admin"))
    assert answer.status_code == 200, answer.text
    body = answer.json()
    assert body["status"] in {"ok", "warning", "critical"}
    assert {check["check"] for check in body["checks"]} >= {"queue_waiting", "jobs_failing"}
    assert body["runbook"].endswith("RUNBOOK.md")
