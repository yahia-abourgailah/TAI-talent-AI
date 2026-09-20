"""A job that is not sales, judged against what it asks for (BR-305, BR-306, CR-05).

The model itself is not called here: a stand-in answers, so these test the platform's rules —
what is written, what it may never carry, and that a person is always left something to do.
"""

import json
import uuid
from collections.abc import Callable
from contextlib import contextmanager

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from api.app import create_app
from assess import worker
from assess.model import Model
from config import Settings
from importer.blobs import MemoryBlobStore
from jobs.queue import RunLog, claim, run_job

from .conftest import sign_in

JOB = {
    "brand": "The Address",
    "department": "Technology",
    "track": "A",
    "headcount": 1,
    "team": "team-tech",
    "title": "Machine Learning Engineer",
    "job_type": "other",
    "description": (
        "We need a machine learning engineer: 3+ years of Python, experience training and "
        "serving models in production, and a degree in computer science or engineering."
    ),
}
ANSWER = {
    "score": 40,
    "summary": "Python and a degree, but no production machine learning.",
    "reasons": [{"says": "Writes Python", "quote": "Python"}],
    "missing": ["models in production"],
}


def _model(answer: dict | str, status: int = 200) -> Callable[[], Model]:
    """A stand-in for the company model. A new client each time, as the worker builds one a job."""

    def handler(request: httpx.Request) -> httpx.Response:
        body = answer if isinstance(answer, str) else json.dumps(answer)
        return httpx.Response(
            status,
            json={
                "model": "gemma-4-stand-in",
                "choices": [{"message": {"content": body}}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 20},
            },
        )

    return lambda: Model(
        "http://model.internal/v1", "gemma-4", transport=httpx.MockTransport(handler)
    )


@pytest.fixture
def api(app_engine):
    connection = app_engine.connect()

    @contextmanager
    def transaction():
        with connection.begin_nested():
            yield connection

    settings = Settings(
        _env_file=None,
        env="dev",
        auth_mode="dev",
        ocr_mode="fake",
        db_dsn="postgresql+psycopg://unused:unused@localhost:1/unused",
        redis_url="redis://localhost:1/0",
        blob_endpoint="http://localhost:1",
        blob_access_key="unused",
        blob_secret_key="unused",
        vllm_base_url="http://model.internal/v1",
    )
    app = create_app(settings, probes={}, transaction=transaction, blobs=MemoryBlobStore())
    try:
        yield TestClient(app), connection, settings
    finally:
        connection.rollback()
        connection.close()


def _applied(client, connection, make_candidate, **job) -> tuple[dict, int]:
    headers = sign_in(client, "ta-lead")
    requisition = client.post(
        "/v1/requisitions",
        json={**JOB, **job},
        headers={**headers, "Idempotency-Key": str(uuid.uuid4())},
    )
    assert requisition.status_code == 201, requisition.text
    candidate = make_candidate(
        connection,
        full_name="Made Up Engineer",
        current_title="Senior Software Tester",
        current_employer="Invented Travel Co",
        education="Bachelor of computer science",
        location="Nasr City, Cairo",
        cv_text="Wrote automated test suites in Python. No machine learning experience.",
    )
    application = client.post(
        "/v1/applications",
        json={"requisition_id": requisition.json()["id"], "candidate_id": f"cand_{candidate}"},
        headers={**headers, "Idempotency-Key": str(uuid.uuid4())},
    )
    assert application.status_code == 201, application.text
    return application.json(), candidate


def _queued(connection, application_id: str, kind: str):
    return connection.execute(
        text(
            "SELECT id FROM jobs.job WHERE kind = :kind AND status = 'queued' "
            "AND params ->> 'application_id' = :application"
        ),
        {"kind": kind, "application": application_id.removeprefix("app_")},
    ).scalar_one_or_none()


def _assess(connection, settings, application_id: str, model: Callable[[], Model]):
    job_id = _queued(connection, application_id, worker.JOB_KIND)
    assert job_id is not None, "an application to a job that is not sales queues an assessment"
    job = claim(connection, job_id)
    assert job is not None
    original = worker.model_from_settings
    worker.model_from_settings = lambda _settings: model()  # type: ignore[assignment]
    try:
        return run_job(connection, job, {worker.JOB_KIND: worker.handle})
    finally:
        worker.model_from_settings = original  # type: ignore[assignment]


def _again(connection, application_id: str, model: Callable[[], Model]) -> None:
    """A second reading of the same application, without going through the queue."""
    original = worker.model_from_settings
    worker.model_from_settings = lambda _settings: model()  # type: ignore[assignment]
    try:
        worker.assess_application(connection, int(application_id.removeprefix("app_")), RunLog())
    finally:
        worker.model_from_settings = original  # type: ignore[assignment]


def _items_about(client, headers, candidate: int) -> list[dict]:
    """The assessment items waiting about one candidate. The database this runs against holds
    other people's items too, so nothing here counts the whole queue."""
    waiting = client.get(
        "/v1/candidate-review-items", params={"kind": "ai_assessment"}, headers=headers
    ).json()["items"]
    return [item for item in waiting if item["candidate_id"] == f"cand_{candidate}"]


def test_a_job_that_is_not_sales_is_assessed_not_scored(api, make_candidate):
    client, connection, settings = api
    application, candidate = _applied(client, connection, make_candidate)

    # The criteria version is not run for these: it scores a sales hire and nothing else.
    assert _queued(connection, application["id"], "score_application") is None
    _assess(connection, settings, application["id"], _model(ANSWER))

    headers = sign_in(client, "ta-lead")
    evaluations = client.get(
        f"/v1/candidates/cand_{candidate}/evaluations", headers=headers
    ).json()["items"]
    assert len(evaluations) == 1
    found = evaluations[0]
    assert found["origin"] == "ai"
    assert found["application_id"] == application["id"]
    assert found["track"] == "not_applicable"
    assert found["score"] == 40
    assert found["model_version"] == "gemma-4-stand-in"
    assert found["prompt_version"]
    # BR-306: an AI never sets a tier, and the database will not hold one.
    assert found["tier"] is None
    assert found["call_priority"] is None
    assert any("Writes Python" in signal for signal in found["signals"])
    assert found["flags"] == ["The job asks for: models in production"]


def test_the_database_refuses_an_ai_evaluation_that_carries_a_tier(api, make_candidate):
    """The rules are not left to the code that writes them (BR-305, BR-306)."""
    client, connection, _settings = api
    application, candidate = _applied(client, connection, make_candidate)
    insert = text(
        "INSERT INTO core.evaluation (candidate_id, application_id, criteria_version_id, origin, "
        "score, tier, model_version, prompt_version, recorded_by) "
        "SELECT :c, :a, id, 'ai', 80, :tier, 'gemma-4', 'p/1', 'test' "
        "FROM core.criteria_version LIMIT 1"
    )
    values = {"c": candidate, "a": int(application["id"].removeprefix("app_")), "tier": "P1"}
    with (
        pytest.raises(DBAPIError, match="evaluation_ai_never_sets_a_tier"),
        connection.begin_nested(),
    ):
        connection.execute(insert, values)
    # and an assessment that does not name the job it read the CV against is refused too
    with (
        pytest.raises(DBAPIError, match="evaluation_ai_names_the_application"),
        connection.begin_nested(),
    ):
        connection.execute(insert, {**values, "a": None, "tier": None})


def test_an_assessment_always_leaves_a_person_something_to_do(api, make_candidate):
    client, connection, settings = api
    application, candidate = _applied(client, connection, make_candidate)
    _assess(connection, settings, application["id"], _model(ANSWER))

    headers = sign_in(client, "ta-lead")
    waiting = client.get(
        "/v1/review-queue", params={"kind": "ai_assessment"}, headers=headers
    ).json()["items"]
    mine = [item for item in waiting if item["application_id"] == application["id"]]
    assert len(mine) == 1, "one item per application, however many times it is assessed"
    assert "40 out of 100" in mine[0]["reason"]
    assert mine[0]["resolve_at"].startswith("/v1/candidate-review-items/")

    # assessing the same application again does not put a second line on anybody's queue
    _again(connection, application["id"], _model(ANSWER))
    again = client.get(
        "/v1/review-queue", params={"kind": "ai_assessment"}, headers=headers
    ).json()["items"]
    assert len([i for i in again if i["application_id"] == application["id"]]) == 1

    item = _items_about(client, headers, candidate)[0]
    assert item["assessment"]["application_id"] == application["id"]
    assert item["assessment"]["score"] == 40


def test_an_answer_the_cv_does_not_support_leaves_the_cv_to_a_person_with_no_score(
    api, make_candidate
):
    client, connection, settings = api
    application, candidate = _applied(client, connection, make_candidate)
    invented = {**ANSWER, "reasons": [{"says": "Ran a team", "quote": "Head of Engineering"}]}
    _assess(connection, settings, application["id"], _model(invented))

    headers = sign_in(client, "ta-lead")
    evaluations = client.get(
        f"/v1/candidates/cand_{candidate}/evaluations", headers=headers
    ).json()["items"]
    assert evaluations == [], "nothing is recorded from an answer about another document"
    mine = _items_about(client, headers, candidate)
    assert len(mine) == 1
    assert mine[0]["assessment"] is None, "no score is attached to an answer we could not check"


def test_a_sales_job_is_untouched_by_any_of_this(api, make_candidate):
    client, connection, _settings = api
    application, _candidate = _applied(
        client, connection, make_candidate, job_type="sales", description=None
    )
    assert _queued(connection, application["id"], "score_application") is not None
    assert _queued(connection, application["id"], worker.JOB_KIND) is None


def test_a_job_that_is_not_sales_must_say_what_it_asks_for(api):
    client, _connection, _settings = api
    refused = client.post(
        "/v1/requisitions",
        json={**JOB, "description": None},
        headers={**sign_in(client, "ta-lead"), "Idempotency-Key": str(uuid.uuid4())},
    )
    assert refused.status_code == 400
    assert refused.json()["error"]["code"] == "invalid_request"


def test_a_model_that_is_down_is_waited_for_then_handed_to_a_person(api, make_candidate):
    """A CV is never lost because a machine was busy, and never judged by silence."""
    client, connection, settings = api
    application, candidate = _applied(client, connection, make_candidate)
    down = _model("", status=503)

    _assess(connection, settings, application["id"], down)
    waiting_again = connection.execute(
        text(
            "SELECT params, run_after FROM jobs.job WHERE kind = :kind AND status = 'queued' "
            "AND params ->> 'application_id' = :application"
        ),
        {"kind": worker.JOB_KIND, "application": application["id"].removeprefix("app_")},
    ).one()
    assert waiting_again.params["attempt"] == 2, "it tries again rather than giving up"
    assert waiting_again.run_after is not None, "and waits before it does"

    headers = sign_in(client, "ta-lead")
    assert _items_about(client, headers, candidate) == [], (
        "nobody is asked to do anything while it is still trying"
    )

    for _ in range(worker.MAX_ATTEMPTS - 1):
        # the wait itself is not what is being tested: bring each retry forward and run it
        connection.execute(
            text("UPDATE jobs.job SET run_after = clock_timestamp() WHERE status = 'queued'")
        )
        _assess(connection, settings, application["id"], down)

    mine = _items_about(client, headers, candidate)
    assert len(mine) == 1, "when it gives up, the CV goes to a person"
    assert mine[0]["assessment"] is None, "with no score, because there is none"
    assert (
        client.get(f"/v1/candidates/cand_{candidate}/evaluations", headers=headers).json()["items"]
        == []
    )
