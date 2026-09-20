"""A job that is not sales, matched against the skills it asks for (BR-305, BR-306, CR-05).

The CV service is not called here: a stand-in answers, so these test the platform's rules — what
is written, what it may never carry, and that a person is always left something to do.
"""

import hashlib
import json
import uuid
from contextlib import contextmanager

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from api.app import create_app
from assess import worker
from assess.match import Matcher
from config import Settings
from importer.blobs import MemoryBlobStore
from jobs.queue import RunLog, claim, run_job

from .conftest import sign_in

CV = (
    b"Made Up Engineer\nSoftware Engineer, Invented Software Co, 2022-2026\n"
    b"Wrote services in Python. Shipped them with Docker.\n"
    b"Bachelor of computer science, Cairo University.\n"
)
JOB = {
    "brand": "The Address",
    "department": "Technology",
    "track": "A",
    "headcount": 1,
    "team": "team-tech",
    "title": "Machine Learning Engineer",
    "job_type": "other",
    "description": (
        "We need a machine learning engineer: Python, models in production, and a degree in "
        "computer science or engineering."
    ),
    "requirements": [
        {"skill": "Python", "level": "advanced"},
        {"skill": "Docker", "level": "intermediate", "category": "Technical"},
        {"skill": "Machine Learning", "level": "advanced"},
    ],
}
REPORT = {
    "cv": {"name": "Made Up Engineer"},
    "match_report": {
        "job_title": "Machine Learning Engineer",
        "overall_match_percentage": 66.67,
        "matched": [
            {
                "category": "Technical",
                "skill": "Python",
                "required_level": "advanced",
                "detected_level": "advanced",
                "source": "explicit",
                "evidence": "Wrote services in Python",
                "status": "matched",
                "gap": 0,
            },
            {
                "category": "Technical",
                "skill": "Docker",
                "required_level": "intermediate",
                "detected_level": "advanced",
                "source": "experience",
                "evidence": "Shipped them with Docker",
                "status": "matched",
                "gap": 0,
            },
        ],
        "below": [],
        "missing": [
            {
                "category": "Technical",
                "skill": "Machine Learning",
                "required_level": "advanced",
                "detected_level": None,
                "source": "unknown",
                "evidence": None,
                "status": "missing",
                "gap": 3,
            }
        ],
    },
    "metadata": {"extraction_method": "pdf_parser", "requirements_hash": "e7259d6669529e07"},
}


def _service(answer: dict | str, status: int = 200):
    """A stand-in for the CV service. A new client each time, as the worker builds one a job."""

    def handler(request: httpx.Request) -> httpx.Response:
        body = answer if isinstance(answer, str) else json.dumps(answer)
        return httpx.Response(
            status, content=body.encode(), headers={"content-type": "application/json"}
        )

    return lambda: Matcher(
        "http://cv-service.internal", api_key="made-up", transport=httpx.MockTransport(handler)
    )


@pytest.fixture
def api(app_engine):
    connection = app_engine.connect()
    blobs = MemoryBlobStore()

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
    )
    app = create_app(settings, probes={}, transaction=transaction, blobs=blobs)
    try:
        yield TestClient(app), connection, settings, blobs
    finally:
        connection.rollback()
        connection.close()


def _candidate_with_a_cv(connection, blobs, content: bytes = CV, **fields) -> int:
    """A candidate as the careers page leaves one: made from their own CV file, which is in the
    store and named by the capture the record was created from."""
    key = f"test/{uuid.uuid4().hex}.pdf"
    blobs.put_if_absent(key, content, "application/pdf")
    capture = connection.execute(
        text(
            """
            INSERT INTO raw.capture
              (source, external_id, content_sha256, blob_key, media_type, byte_size, received_by)
            VALUES ('cv_upload', :ext, :sha, :key, 'application/pdf', :size, 'test')
            RETURNING id
            """
        ),
        {
            "ext": uuid.uuid4().hex,
            "sha": hashlib.sha256(content + uuid.uuid4().bytes).digest(),
            "key": key,
            "size": len(content),
        },
    ).scalar_one()
    candidate = int(
        connection.execute(
            text(
                "INSERT INTO core.candidate (capture_id, source_key, created_by, pipeline_state) "
                "VALUES (:capture, :key, 'integration-test', 'not_recorded') RETURNING id"
            ),
            {"capture": capture, "key": f"test:{uuid.uuid4().hex}"},
        ).scalar_one()
    )
    for field, value in fields.items():
        connection.execute(
            text(
                "INSERT INTO core.candidate_field "
                "(candidate_id, field, value, source, verification_status, recorded_by) "
                "VALUES (:c, :f, :v, 'cv_extraction', 'unverified', 'integration-test')"
            ),
            {"c": candidate, "f": field, "v": value},
        )
    connection.execute(
        text(
            "INSERT INTO intake.cv_upload (capture_id, candidate_id, token_sha256, expires_at) "
            "VALUES (:c, :cand, :tok, clock_timestamp() + interval '1 day')"
        ),
        {
            "c": capture,
            "cand": candidate,
            "tok": hashlib.sha256(uuid.uuid4().bytes).digest(),
        },
    )
    return candidate


def _applied(client, connection, blobs, make_candidate, *, with_cv=True, **job):
    headers = sign_in(client, "ta-lead")
    requisition = client.post(
        "/v1/requisitions",
        json={**JOB, **job},
        headers={**headers, "Idempotency-Key": str(uuid.uuid4())},
    )
    assert requisition.status_code == 201, requisition.text
    fields = {
        "full_name": "Made Up Engineer",
        "current_title": "Software Engineer",
        "current_employer": "Invented Software Co",
        "education": "Bachelor of computer science",
        "location": "Nasr City, Cairo",
    }
    candidate = (
        _candidate_with_a_cv(connection, blobs, **fields)
        if with_cv
        else make_candidate(connection, **fields)
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


def _assess(connection, blobs, application_id: str, service):
    job_id = _queued(connection, application_id, worker.JOB_KIND)
    assert job_id is not None, "an application to a job that is not sales queues an assessment"
    job = claim(connection, job_id)
    assert job is not None
    original_matcher, original_blobs = worker.matcher_from_settings, worker.blobs_from_settings
    worker.matcher_from_settings = lambda _settings: service()  # type: ignore[assignment]
    worker.blobs_from_settings = lambda _settings: blobs  # type: ignore[assignment]
    try:
        return run_job(connection, job, {worker.JOB_KIND: worker.handle})
    finally:
        worker.matcher_from_settings = original_matcher  # type: ignore[assignment]
        worker.blobs_from_settings = original_blobs  # type: ignore[assignment]


def _again(connection, blobs, application_id: str, service) -> None:
    """A second match of the same application, without going through the queue."""
    original = worker.matcher_from_settings
    worker.matcher_from_settings = lambda _settings: service()  # type: ignore[assignment]
    try:
        worker.assess_application(
            connection, int(application_id.removeprefix("app_")), RunLog(), blobs=blobs
        )
    finally:
        worker.matcher_from_settings = original  # type: ignore[assignment]


def _items_about(client, headers, candidate: int) -> list[dict]:
    """The assessment items waiting about one candidate. The database this runs against holds
    other people's items too, so nothing here counts the whole queue."""
    waiting = client.get(
        "/v1/candidate-review-items", params={"kind": "ai_assessment"}, headers=headers
    ).json()["items"]
    return [item for item in waiting if item["candidate_id"] == f"cand_{candidate}"]


def test_a_job_that_is_not_sales_is_matched_not_scored(api, make_candidate):
    client, connection, _settings, blobs = api
    application, candidate = _applied(client, connection, blobs, make_candidate)

    # The criteria version is not run for these: it scores a sales hire and nothing else.
    assert _queued(connection, application["id"], "score_application") is None
    _assess(connection, blobs, application["id"], _service(REPORT))

    headers = sign_in(client, "ta-lead")
    evaluations = client.get(
        f"/v1/candidates/cand_{candidate}/evaluations", headers=headers
    ).json()["items"]
    assert len(evaluations) == 1
    found = evaluations[0]
    assert found["origin"] == "ai"
    assert found["score"] == 66.67
    assert found["application_id"] == application["id"]
    assert found["track"] == "not_applicable"
    assert found["model_version"] == "cv-service/pdf_parser"
    # The exact list that was graded, named on the evaluation: the opening's list never changes,
    # and this says which one it was.
    assert found["prompt_version"].endswith("+e7259d6669529e07")
    # BR-306: an AI never sets a tier, and the database will not hold one.
    assert found["tier"] is None
    assert found["call_priority"] is None
    assert found["signals"] == [
        "Python: advanced, advanced asked for — “Wrote services in Python”",
        "Docker: advanced, intermediate asked for — “Shipped them with Docker”",
    ]
    assert found["flags"] == ["Machine Learning: not in the CV, advanced asked for"]
    assert found["recommendation"] == "2 of the 3 skills this job asks for are shown in the CV."


def test_what_is_sent_is_the_job_s_own_list(api, make_candidate):
    """The requirements come from the requisition, never from the request that asks for a match."""
    client, connection, _settings, blobs = api
    application, _candidate = _applied(client, connection, blobs, make_candidate)
    sent: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        body = request.content.decode("utf-8", "ignore")
        start = body.index('{"job_title"')
        sent.update(json.loads(body[start : body.index("\r\n--", start)]))
        return httpx.Response(200, json=REPORT)

    _assess(
        connection,
        blobs,
        application["id"],
        lambda: Matcher("http://cv-service.internal", transport=httpx.MockTransport(handler)),
    )
    assert sent["job_title"] == "Machine Learning Engineer"
    groups = sent["skill_types"]
    assert [skill["name"] for group in groups for skill in group["skills"]] == [
        "Python",
        "Docker",
        "Machine Learning",
    ]
    assert [skill["required_level"] for group in groups for skill in group["skills"]] == [
        "advanced",
        "intermediate",
        "advanced",
    ]


def test_the_database_refuses_an_ai_evaluation_that_carries_a_tier(api, make_candidate):
    """The rules are not left to the code that writes them (BR-305, BR-306)."""
    client, connection, _settings, blobs = api
    application, candidate = _applied(client, connection, blobs, make_candidate)
    insert = text(
        "INSERT INTO core.evaluation (candidate_id, application_id, criteria_version_id, origin, "
        "score, tier, model_version, prompt_version, recorded_by) "
        "SELECT :c, :a, id, 'ai', 80, :tier, 'cv-service', 'p/1', 'test' "
        "FROM core.criteria_version LIMIT 1"
    )
    values = {"c": candidate, "a": int(application["id"].removeprefix("app_")), "tier": "P1"}
    with (
        pytest.raises(DBAPIError, match="evaluation_ai_never_sets_a_tier"),
        connection.begin_nested(),
    ):
        connection.execute(insert, values)
    # and an assessment that does not name the job it matched the CV against is refused too
    with (
        pytest.raises(DBAPIError, match="evaluation_ai_names_the_application"),
        connection.begin_nested(),
    ):
        connection.execute(insert, {**values, "a": None, "tier": None})


def test_a_match_always_leaves_a_person_something_to_do(api, make_candidate):
    client, connection, _settings, blobs = api
    application, candidate = _applied(client, connection, blobs, make_candidate)
    _assess(connection, blobs, application["id"], _service(REPORT))

    headers = sign_in(client, "ta-lead")
    waiting = client.get(
        "/v1/review-queue", params={"kind": "ai_assessment"}, headers=headers
    ).json()["items"]
    mine = [item for item in waiting if item["application_id"] == application["id"]]
    assert len(mine) == 1, "one item per application, however many times it is matched"
    assert "67 out of 100" in mine[0]["reason"]
    assert mine[0]["resolve_at"].startswith("/v1/candidate-review-items/")

    # matching the same application again does not put a second line on anybody's queue
    _again(connection, blobs, application["id"], _service(REPORT))
    again = client.get(
        "/v1/review-queue", params={"kind": "ai_assessment"}, headers=headers
    ).json()["items"]
    assert len([i for i in again if i["application_id"] == application["id"]]) == 1

    item = _items_about(client, headers, candidate)[0]
    assert item["assessment"]["application_id"] == application["id"]
    assert item["assessment"]["score"] == 67


def test_an_answer_we_cannot_tie_to_the_skills_leaves_the_cv_to_a_person(api, make_candidate):
    client, connection, _settings, blobs = api
    application, candidate = _applied(client, connection, blobs, make_candidate)
    nothing = {"cv": {}, "match_report": {"overall_match_percentage": 90.0}, "metadata": {}}
    _assess(connection, blobs, application["id"], _service(nothing))

    headers = sign_in(client, "ta-lead")
    assert (
        client.get(f"/v1/candidates/cand_{candidate}/evaluations", headers=headers).json()["items"]
        == []
    ), "a percentage that names no skill is not recorded"
    mine = _items_about(client, headers, candidate)
    assert len(mine) == 1
    assert mine[0]["assessment"] is None, "no score is attached to an answer we could not read"


def test_a_candidate_with_no_cv_file_goes_to_a_person(api, make_candidate):
    """Most of the 5,140 records came from a spreadsheet and have no file to send (BR-703)."""
    client, connection, _settings, blobs = api
    application, candidate = _applied(client, connection, blobs, make_candidate, with_cv=False)
    _assess(connection, blobs, application["id"], _service(REPORT))

    headers = sign_in(client, "ta-lead")
    assert (
        client.get(f"/v1/candidates/cand_{candidate}/evaluations", headers=headers).json()["items"]
        == []
    )
    assert len(_items_about(client, headers, candidate)) == 1


def test_a_sales_job_is_untouched_by_any_of_this(api, make_candidate):
    client, connection, _settings, blobs = api
    application, _candidate = _applied(
        client,
        connection,
        blobs,
        make_candidate,
        job_type="sales",
        description=None,
        requirements=None,
    )
    assert _queued(connection, application["id"], "score_application") is not None
    assert _queued(connection, application["id"], worker.JOB_KIND) is None


def test_a_service_that_is_down_is_waited_for_then_handed_to_a_person(api, make_candidate):
    """A CV is never lost because a machine was busy, and never judged by silence."""
    client, connection, _settings, blobs = api
    application, candidate = _applied(client, connection, blobs, make_candidate)
    down = _service("", status=503)

    _assess(connection, blobs, application["id"], down)
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
        _assess(connection, blobs, application["id"], down)

    mine = _items_about(client, headers, candidate)
    assert len(mine) == 1, "when it gives up, the CV goes to a person"
    assert mine[0]["assessment"] is None, "with no score, because there is none"
    assert (
        client.get(f"/v1/candidates/cand_{candidate}/evaluations", headers=headers).json()["items"]
        == []
    )


@pytest.mark.parametrize(
    ("change", "because"),
    [
        ({"requirements": None}, "a job that is not sales needs the skills"),
        ({"description": None}, "a job that is not sales needs a description"),
        (
            {"requirements": [{"skill": "Python", "level": "wizard"}]},
            "level",
        ),
        (
            {
                "requirements": [
                    {"skill": "Python", "level": "advanced"},
                    {"skill": " python ", "level": "expert"},
                ]
            },
            "the same skill is asked for twice",
        ),
    ],
)
def test_a_job_that_is_not_sales_must_say_what_it_asks_for(api, change, because):
    client, _connection, _settings, _blobs = api
    refused = client.post(
        "/v1/requisitions",
        json={**JOB, **change},
        headers={**sign_in(client, "ta-lead"), "Idempotency-Key": str(uuid.uuid4())},
    )
    assert refused.status_code == 400
    assert refused.json()["error"]["code"] == "invalid_request"


def test_a_sales_job_may_not_carry_a_skills_list(api):
    client, _connection, _settings, _blobs = api
    refused = client.post(
        "/v1/requisitions",
        json={**JOB, "job_type": "sales", "description": None},
        headers={**sign_in(client, "ta-lead"), "Idempotency-Key": str(uuid.uuid4())},
    )
    assert refused.status_code == 400
