"""A worker takes only jobs it has a handler for, and leaves every other kind queued untouched.
Everything runs in one transaction that is rolled back."""

import uuid

from sqlalchemy import text

from jobs.queue import claim, enqueue


def _status(conn, job_id: int) -> str:
    return str(
        conn.execute(
            text("SELECT status FROM jobs.job WHERE id = :id"), {"id": job_id}
        ).scalar_one()
    )


def test_a_worker_only_claims_the_kinds_it_can_run(app_engine):
    mine, other = f"test-kind-{uuid.uuid4().hex[:8]}", f"test-kind-{uuid.uuid4().hex[:8]}"
    with app_engine.connect() as conn:
        theirs = enqueue(conn, other, {}, "integration-test")  # queued first, so oldest
        ours = enqueue(conn, mine, {}, "integration-test")

        job = claim(conn, kinds=[mine])
        assert job is not None and job.id == ours
        assert _status(conn, theirs) == "queued"
        assert claim(conn, kinds=[mine]) is None


def test_asking_for_a_job_of_another_kind_claims_nothing(app_engine):
    with app_engine.connect() as conn:
        job_id = enqueue(conn, f"test-kind-{uuid.uuid4().hex[:8]}", {}, "integration-test")
        assert claim(conn, job_id, kinds=["score_application"]) is None
        assert _status(conn, job_id) == "queued"
