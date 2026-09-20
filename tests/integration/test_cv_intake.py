"""Week 5, Person B: a CV is uploaded, kept forever, read into a form, and sent to a person when
anything goes wrong (BR-102, BR-106, BR-107, BR-308, NFR-01, NFR-04). Made-up files only; the fake
OCR stands in for the API. Everything rolls back.
"""

import hashlib
import uuid
from contextlib import contextmanager

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from api.app import create_app
from config import Environment, Settings
from importer.blobs import MemoryBlobStore
from intake import reading
from intake.fake_ocr import FakeOcrReader
from intake.ocr import OcrLimits
from jobs.queue import RunLog, claim

from .conftest import sign_in

LIMITS = OcrLimits(max_concurrency=2, max_attempts=3, retry_gaps_seconds=(30, 120))


@pytest.fixture
def intake(app_engine):
    """The app with an in-memory file store, on one connection rolled back at the end."""
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
        # Pinned: without it the developer's own TALENT_OCR_MODE decides what these tests mean.
        ocr_mode="fake",
        db_dsn="postgresql+psycopg://unused:unused@localhost:1/unused",
        redis_url="redis://localhost:1/0",
        blob_endpoint="http://localhost:1",
        blob_access_key="unused",
        blob_secret_key="unused",
    )
    app = create_app(settings, probes={}, transaction=transaction, blobs=blobs)
    try:
        yield TestClient(app), connection, blobs
    finally:
        connection.rollback()
        connection.close()


def cv(*markers: str) -> bytes:
    """A made-up PDF, unique per call. Markers steer the fake OCR."""
    words = " ".join(f"FAKE-OCR:{m}" for m in markers)
    return f"%PDF-1.4\n% made-up test CV {uuid.uuid4().hex} {words}\n".encode()


def upload(client: TestClient, content: bytes, name: str = "cv.pdf"):
    response = client.post(
        "/v1/public/cv-uploads", files={"file": (name, content, "application/pdf")}
    )
    assert response.status_code == 201, response.text
    return response.json()


def status(client: TestClient, body: dict, token: str | None = None):
    return client.get(
        f"/v1/public/cv-uploads/{body['upload_id']}",
        headers={"X-Upload-Token": token or body["upload_token"]},
    )


def upload_row(connection, body: dict):
    number = int(body["upload_id"].removeprefix("upl_"))
    return connection.execute(
        text("SELECT capture_id, candidate_id FROM intake.cv_upload WHERE id = :id"),
        {"id": number},
    ).one()


def read_jobs(connection, capture_id: int):
    return connection.execute(
        text(
            "SELECT id, status, params, run_after IS NOT NULL AS delayed FROM jobs.job "
            "WHERE kind = 'read_cv' AND params ->> 'capture_id' = :c ORDER BY id"
        ),
        {"c": str(capture_id)},
    ).all()


def services(blobs, behaviour: str | None = None) -> reading.Services:
    return reading.Services(
        blobs=blobs, reader=FakeOcrReader(Environment.DEV, behaviour=behaviour), limits=LIMITS
    )


def run_read(connection, capture_id: int, svc, attempt: int = 1) -> RunLog:
    log = RunLog()
    reading.read_cv(connection, capture_id, attempt, log, svc)
    return log


def document_items(connection, capture_id: int):
    return connection.execute(
        text(
            "SELECT kind, reason_code, candidate_id, application_id FROM pipeline.review_item "
            "WHERE capture_id = :c ORDER BY id"
        ),
        {"c": capture_id},
    ).all()


def reading_row(connection, capture_id: int):
    return connection.execute(
        text(
            "SELECT outcome, failure, hidden_content, answer_capture_id, reader, attempts "
            "FROM intake.cv_reading WHERE capture_id = :c ORDER BY id DESC LIMIT 1"
        ),
        {"c": capture_id},
    ).one_or_none()


# --- B1: upload, and keep the original forever ---------------------------------------------------


def test_an_uploaded_cv_is_kept_byte_for_byte_and_queued_for_reading(intake):
    client, connection, blobs = intake
    content = cv("ar")
    body = upload(client, content)

    assert set(body) == {"upload_id", "upload_token", "status", "expires_at"}
    assert body["upload_id"].startswith("upl_") and body["status"] == "processing"
    row = upload_row(connection, body)
    capture = connection.execute(
        text("SELECT source, blob_key, media_type, byte_size FROM raw.capture WHERE id = :id"),
        {"id": row.capture_id},
    ).one()
    assert (capture.source, capture.media_type, capture.byte_size) == (
        "cv_upload",
        "application/pdf",
        len(content),
    )
    assert blobs.get(capture.blob_key) == content
    candidate_source = connection.execute(
        text(
            "SELECT r.source FROM core.candidate c JOIN raw.capture r ON r.id = c.capture_id "
            "WHERE c.id = :id"
        ),
        {"id": row.candidate_id},
    ).scalar_one()
    assert candidate_source == "cv_upload"
    (job,) = read_jobs(connection, row.capture_id)
    assert (job.status, job.params) == ("queued", {"capture_id": row.capture_id, "attempt": 1})
    assert status(client, body).json()["status"] == "processing"
    # The token is kept only as its hash.
    stored = connection.execute(
        text("SELECT token_sha256 FROM intake.cv_upload WHERE capture_id = :c"),
        {"c": row.capture_id},
    ).scalar_one()
    assert body["upload_token"].encode() not in bytes(stored)


def test_a_read_cv_comes_back_as_a_filled_form_with_the_name_as_written(intake):
    client, connection, blobs = intake
    body = upload(client, cv("ar"))
    row = upload_row(connection, body)
    log = run_read(connection, row.capture_id, services(blobs))
    assert log.counts["cvs_read"] == 1

    ready = status(client, body)
    assert ready.status_code == 200
    assert ready.headers["cache-control"] == "no-store"
    form = ready.json()
    assert form["status"] == "ready"
    fields = form["fields"]
    assert fields["full_name"] == {
        "value": "مرشح  تجريبي الأول",
        "inference": "stated",
        "language": "ar",
    }
    assert fields["age"] == {"value": "25", "inference": "inferred"}
    assert fields["email"] == {"value": None, "state": "not_recorded"}

    stored = connection.execute(
        text(
            "SELECT source, verification_status, recorded_by, source_ref "
            "FROM core.candidate_field WHERE candidate_id = :c AND field = 'full_name'"
        ),
        {"c": row.candidate_id},
    ).one()
    assert (stored.source, stored.verification_status, stored.recorded_by) == (
        "cv_extraction",
        "unverified",
        "cv-reader",
    )
    assert stored.source_ref.startswith("intake.cv_reading:")

    kept = reading_row(connection, row.capture_id)
    assert (kept.outcome, kept.reader, kept.attempts, kept.hidden_content) == (
        "read",
        "fake-ocr",
        1,
        False,
    )
    answer = connection.execute(
        text("SELECT source, blob_key FROM raw.capture WHERE id = :id"),
        {"id": kept.answer_capture_id},
    ).one()
    assert answer.source == "ocr_answer"
    assert b'"schema"' in blobs.get(answer.blob_key)  # the answer, exactly as it came
    # Reading again does nothing.
    assert run_read(connection, row.capture_id, services(blobs)).counts == {"already_read": 1}


def test_a_wrong_or_missing_token_reads_as_not_found(intake):
    client, _connection, _blobs = intake
    body = upload(client, cv())
    assert status(client, body, token="not-the-token").status_code == 404
    other = upload(client, cv())
    assert status(client, {**body, "upload_id": other["upload_id"]}).status_code == 404


def test_a_wrong_file_type_is_refused_and_nothing_is_kept(intake):
    client, connection, blobs = intake
    before = connection.execute(text("SELECT count(*) FROM raw.capture")).scalar_one()
    response = client.post(
        "/v1/public/cv-uploads",
        files={"file": ("cv.pdf", b"#!/bin/sh made-up script", "application/pdf")},
    )
    assert response.status_code == 415
    assert connection.execute(text("SELECT count(*) FROM raw.capture")).scalar_one() == before
    assert blobs.objects == {}


def test_the_stored_original_cannot_be_changed_or_deleted(intake, owner_engine):
    client, connection, blobs = intake
    # Not even the owner can empty the upload or reading records. Checked before this test's own
    # transaction holds any lock on them.
    with owner_engine.connect() as owner:
        for table in ("intake.cv_upload", "intake.cv_reading"):
            transaction = owner.begin()
            try:
                owner.execute(text("SET LOCAL lock_timeout = '5s'"))
                with pytest.raises(DBAPIError, match="append-only"):
                    owner.execute(text(f"TRUNCATE {table}"))
            finally:
                transaction.rollback()

    content = cv()
    row = upload_row(connection, upload(client, content))
    key = connection.execute(
        text("SELECT blob_key FROM raw.capture WHERE id = :id"), {"id": row.capture_id}
    ).scalar_one()

    for statement in (
        "UPDATE raw.capture SET byte_size = 1 WHERE id = :id",
        "DELETE FROM raw.capture WHERE id = :id",
        "UPDATE intake.cv_upload SET expires_at = now() WHERE capture_id = :id",
        "DELETE FROM intake.cv_upload WHERE capture_id = :id",
    ):
        with pytest.raises(DBAPIError), connection.begin_nested():
            connection.execute(text(statement), {"id": row.capture_id})

    # The object store never overwrites a key, and the key is the content's hash.
    assert blobs.put_if_absent(key, b"something else", "application/pdf") is False
    assert blobs.get(key) == content


# --- B2: the same file twice is one candidate ----------------------------------------------------


def test_the_same_file_twice_even_renamed_is_one_capture_and_one_candidate(intake):
    client, connection, _blobs = intake
    content = cv("en")
    first = upload(client, content, "Made-up CV.pdf")
    second = upload(client, content, "renamed-final-v2.pdf")

    assert first["upload_id"] != second["upload_id"]
    assert first["upload_token"] != second["upload_token"]
    one, two = upload_row(connection, first), upload_row(connection, second)
    assert (one.capture_id, one.candidate_id) == (two.capture_id, two.candidate_id)
    assert len(read_jobs(connection, one.capture_id)) == 1
    captures = connection.execute(
        text(
            "SELECT count(*) FROM raw.capture WHERE source = 'cv_upload' AND content_sha256 = "
            "(SELECT content_sha256 FROM raw.capture WHERE id = :id)"
        ),
        {"id": one.capture_id},
    ).scalar_one()
    candidates = connection.execute(
        text("SELECT count(*) FROM core.candidate WHERE capture_id = :id"), {"id": one.capture_id}
    ).scalar_one()
    assert (captures, candidates) == (1, 1)
    # Both tokens see the same reading once it is done.
    assert status(client, first).json()["status"] == status(client, second).json()["status"]


# --- B3: limits, retries, and nothing lost -------------------------------------------------------


@pytest.mark.parametrize(
    ("behaviour", "reason"), [("fail", "ocr_unavailable"), ("timeout", "ocr_timed_out")]
)
def test_a_failing_ocr_is_retried_with_gaps_then_the_cv_goes_to_a_person(intake, behaviour, reason):
    client, connection, blobs = intake
    body = upload(client, cv(behaviour))
    row = upload_row(connection, body)
    svc = services(blobs)

    first = run_read(connection, row.capture_id, svc, attempt=1)
    assert first.counts == {reason: 1, "retries_queued": 1}
    jobs = read_jobs(connection, row.capture_id)
    assert [(j.params["attempt"], j.delayed) for j in jobs] == [(1, False), (2, True)]
    assert reading_row(connection, row.capture_id) is None
    assert status(client, body).json()["status"] == "processing"

    run_read(connection, row.capture_id, svc, attempt=2)
    last = run_read(connection, row.capture_id, svc, attempt=LIMITS.max_attempts)
    assert last.counts == {reason: 1, f"failed_{reason}": 1, "review_items_opened": 1}

    kept = reading_row(connection, row.capture_id)
    assert (kept.outcome, kept.failure, kept.attempts) == ("failed", reason, 3)
    assert [
        (i.kind, i.reason_code, i.candidate_id, i.application_id)
        for i in document_items(connection, row.capture_id)
    ] == [("flagged_document", reason, row.candidate_id, None)]
    failed = status(client, body).json()
    assert (failed["status"], failed["fields"]) == ("failed", None)
    # Nothing lost: the file is still kept.
    key = connection.execute(
        text("SELECT blob_key FROM raw.capture WHERE id = :id"), {"id": row.capture_id}
    ).scalar_one()
    assert blobs.get(key) is not None


def test_a_retry_is_not_claimed_before_its_gap(intake):
    client, connection, blobs = intake
    row = upload_row(connection, upload(client, cv("fail")))
    run_read(connection, row.capture_id, services(blobs))
    first, retry = read_jobs(connection, row.capture_id)
    assert claim(connection, retry.id, kinds=["read_cv"]) is None
    assert claim(connection, first.id, kinds=["read_cv"]) is not None


def test_a_refused_file_goes_to_a_person_at_once(intake):
    client, connection, blobs = intake
    row = upload_row(connection, upload(client, cv("reject")))
    log = run_read(connection, row.capture_id, services(blobs))
    assert "retries_queued" not in log.counts
    assert reading_row(connection, row.capture_id).failure == "ocr_rejected"
    assert [i.reason_code for i in document_items(connection, row.capture_id)] == ["ocr_rejected"]


def test_an_unreadable_answer_is_kept_and_the_cv_goes_to_a_person(intake):
    client, connection, blobs = intake
    row = upload_row(connection, upload(client, cv("garbled")))
    log = run_read(connection, row.capture_id, services(blobs))
    assert log.unresolved[0]["code"] == "ocr_answer_unreadable"
    kept = reading_row(connection, row.capture_id)
    assert (kept.outcome, kept.failure) == ("failed", "ocr_answer_unreadable")
    assert kept.answer_capture_id is not None
    assert [i.reason_code for i in document_items(connection, row.capture_id)] == [
        "ocr_answer_unreadable"
    ]


def test_hidden_content_sends_the_cv_to_a_person_and_the_candidate_is_never_told(intake):
    client, connection, blobs = intake
    body = upload(client, cv("hidden"))
    row = upload_row(connection, body)
    log = run_read(connection, row.capture_id, services(blobs))
    assert log.counts["hidden_content_found"] == 1

    kept = reading_row(connection, row.capture_id)
    assert (kept.outcome, kept.hidden_content) == ("read", True)
    assert [(i.kind, i.reason_code) for i in document_items(connection, row.capture_id)] == [
        ("flagged_document", "hidden_content")
    ]
    seen = status(client, body)
    assert seen.json()["status"] == "ready"
    assert "hidden" not in seen.text.lower()
    assert "flag" not in seen.text.lower()


def test_a_missing_file_goes_to_a_person(intake):
    client, connection, blobs = intake
    row = upload_row(connection, upload(client, cv()))
    blobs.objects.clear()
    run_read(connection, row.capture_id, services(blobs))
    assert reading_row(connection, row.capture_id).failure == "document_missing"


def test_no_more_cvs_are_read_at_once_than_the_limit(intake, app_engine):
    client, connection, blobs = intake
    row = upload_row(connection, upload(client, cv()))
    svc = services(blobs)
    with app_engine.connect() as other, other.begin():
        for slot in range(LIMITS.max_concurrency):
            other.execute(text("SELECT pg_advisory_xact_lock(7304, :s)"), {"s": slot})
        log = run_read(connection, row.capture_id, svc)
    assert log.counts == {"waited_for_a_reading_slot": 1}
    assert svc.reader.calls == 0
    assert [(j.params["attempt"], j.delayed) for j in read_jobs(connection, row.capture_id)] == [
        (1, False),
        (1, True),
    ]
    assert reading_row(connection, row.capture_id) is None


def test_a_cv_no_job_can_read_any_more_goes_to_a_person(intake):
    client, connection, _blobs = intake
    body = upload(client, cv())
    row = upload_row(connection, body)
    waiting = upload_row(connection, upload(client, cv()))
    (job,) = read_jobs(connection, row.capture_id)
    # The job's worker died on every attempt: recover_stopped finally marks it failed.
    connection.execute(
        text("UPDATE jobs.job SET status = 'running' WHERE id = :id"), {"id": job.id}
    )
    connection.execute(text("UPDATE jobs.job SET status = 'failed' WHERE id = :id"), {"id": job.id})

    counts = reading.settle_abandoned(connection)
    assert counts["failed_ocr_failed"] >= 1
    kept = reading_row(connection, row.capture_id)
    assert (kept.outcome, kept.failure, kept.attempts) == ("failed", "ocr_failed", 1)
    assert [i.reason_code for i in document_items(connection, row.capture_id)] == ["ocr_failed"]
    assert status(client, body).json()["status"] == "failed"
    # A CV whose job is still queued is left alone.
    assert reading_row(connection, waiting.capture_id) is None
    assert reading.settle_abandoned(connection).get("failed_ocr_failed") is None


def test_the_worker_runs_read_cv_jobs(intake, monkeypatch):
    client, connection, blobs = intake
    body = upload(client, cv("mixed"))
    row = upload_row(connection, body)
    monkeypatch.setattr(reading, "services", lambda: services(blobs))
    (job,) = read_jobs(connection, row.capture_id)
    claimed = claim(connection, job.id, kinds=["read_cv"])
    from jobs.__main__ import HANDLERS
    from jobs.queue import run_job

    run_id = run_job(connection, claimed, HANDLERS)
    run = connection.execute(
        text("SELECT outcome, counts, unresolved FROM audit.job_run WHERE id = :id"),
        {"id": run_id},
    ).one()
    assert run.outcome == "succeeded"
    assert run.counts["cvs_read"] == 1
    assert run.unresolved == [
        {"code": "unreadable_value", "capture_id": row.capture_id, "field": "years_experience"}
    ]
    assert "مرشح" not in str(run.counts) + str(run.unresolved)
    assert status(client, body).json()["status"] == "ready"


# --- Recruiters read the files, and every access is recorded -------------------------------------


def test_a_ta_lead_lists_and_downloads_a_cv_and_each_access_is_recorded(intake):
    client, connection, blobs = intake
    content = cv("hidden")
    row = upload_row(connection, upload(client, content))
    run_read(connection, row.capture_id, services(blobs))
    lead = sign_in(client, "ta-lead")

    listed = client.get(f"/v1/candidates/cand_{row.candidate_id}/documents", headers=lead)
    assert listed.status_code == 200, listed.text
    (document,) = listed.json()["items"]
    assert document["id"] == f"doc_{row.capture_id}"
    assert (document["media_type"], document["byte_size"]) == ("application/pdf", len(content))
    assert document["reading"]["status"] == "read"
    assert document["reading"]["hidden_content"] is True

    downloaded = client.get(f"/v1/documents/{document['id']}/file", headers=lead)
    assert downloaded.status_code == 200
    assert downloaded.content == content
    assert downloaded.headers["content-type"] == "application/pdf"
    assert downloaded.headers["cache-control"] == "no-store"
    assert f'filename="doc_{row.capture_id}.pdf"' in downloaded.headers["content-disposition"]

    accesses = connection.execute(
        text(
            "SELECT action, accessed_by, capture_id FROM audit.document_access "
            "WHERE candidate_id = :c ORDER BY id"
        ),
        {"c": row.candidate_id},
    ).all()
    assert [tuple(a) for a in accesses] == [
        ("listed", "dev|ta-lead", None),
        ("downloaded", "dev|ta-lead", row.capture_id),
    ]


def test_files_follow_the_candidates_scope(intake):
    client, connection, blobs = intake
    row = upload_row(connection, upload(client, cv()))
    run_read(connection, row.capture_id, services(blobs))
    path = f"/v1/documents/doc_{row.capture_id}/file"
    # No recruiter owns an application for this candidate yet.
    for account, expected in {"recruiter-a": 404, "criteria-owner": 403, "admin": 200}.items():
        assert client.get(path, headers=sign_in(client, account)).status_code == expected, account
    assert client.get(path).status_code == 401
    # The OCR's answer is kept, but it is not a candidate's document.
    answer = reading_row(connection, row.capture_id).answer_capture_id
    admin = sign_in(client, "admin")
    assert client.get(f"/v1/documents/doc_{answer}/file", headers=admin).status_code == 404


# --- The review queue for candidates -------------------------------------------------------------


def test_a_flagged_cv_is_in_the_candidate_review_queue_and_a_person_checks_it(intake):
    client, connection, blobs = intake
    row = upload_row(connection, upload(client, cv("hidden")))
    run_read(connection, row.capture_id, services(blobs))
    lead = sign_in(client, "ta-lead")

    listed = client.get(
        "/v1/candidate-review-items",
        params={"kind": "flagged_document", "candidate_id": f"cand_{row.candidate_id}"},
        headers=lead,
    )
    assert listed.status_code == 200, listed.text
    (item,) = listed.json()["items"]
    assert {k: item[k] for k in ("kind", "candidate_id", "document_id", "reason_code")} == {
        "kind": "flagged_document",
        "candidate_id": f"cand_{row.candidate_id}",
        "document_id": f"doc_{row.capture_id}",
        "reason_code": "hidden_content",
    }
    assert item["resolution"] is None
    # Not an application item: the frozen queue does not list it.
    frozen = client.get("/v1/review-items", params={"limit": 200}, headers=lead).json()["items"]
    assert item["id"] not in {i["id"] for i in frozen}
    # The event says which candidate and file, to a TA lead only.
    event = connection.execute(
        text(
            "SELECT data FROM integration.event WHERE type = 'review.item_created' "
            "AND data ->> 'review_item_id' = :id"
        ),
        {"id": item["id"]},
    ).scalar_one()
    assert event == {
        "review_item_id": item["id"],
        "kind": "flagged_document",
        "candidate_id": f"cand_{row.candidate_id}",
        "application_id": None,
        "document_id": f"doc_{row.capture_id}",
    }

    recruiter = sign_in(client, "recruiter-a")
    path = f"/v1/candidate-review-items/{item['id']}"
    assert client.get(path, headers=recruiter).status_code == 404
    assert client.get(path, headers=sign_in(client, "criteria-owner")).status_code == 200
    refused = client.post(
        f"{path}/resolution",
        json={"decision": "checked"},
        headers=sign_in(client, "criteria-owner"),
    )
    assert refused.status_code == 403

    no_reason = client.post(f"{path}/resolution", json={"decision": "dismiss"}, headers=lead)
    assert no_reason.status_code == 400
    checked = client.post(
        f"{path}/resolution",
        json={"decision": "checked", "reason": "Hidden text removed; CV is fine"},
        headers={**lead, "Idempotency-Key": str(uuid.uuid4())},
    )
    assert checked.status_code == 201, checked.text
    resolution = checked.json()["resolution"]
    assert (resolution["decision"], resolution["resolved_by"]) == ("checked", "dev|ta-lead")
    again = client.post(f"{path}/resolution", json={"decision": "checked"}, headers=lead)
    assert (again.status_code, again.json()["error"]["code"]) == (409, "review_item_resolved")
    open_items = client.get(
        "/v1/candidate-review-items",
        params={"candidate_id": f"cand_{row.candidate_id}"},
        headers=lead,
    ).json()["items"]
    assert open_items == []


def test_a_document_item_cannot_be_confirmed_as_a_rejection(intake):
    client, connection, blobs = intake
    row = upload_row(connection, upload(client, cv("reject")))
    run_read(connection, row.capture_id, services(blobs))
    item = connection.execute(
        text("SELECT id FROM pipeline.review_item WHERE capture_id = :c"), {"c": row.capture_id}
    ).scalar_one()
    with (
        pytest.raises(DBAPIError, match="only a proposed rejection is confirmed"),
        connection.begin_nested(),
    ):
        connection.execute(
            text(
                "INSERT INTO pipeline.review_resolution (review_item_id, outcome, move_id, "
                "resolved_by) VALUES (:i, 'confirmed', NULL, 'x')"
            ),
            {"i": item},
        )
    with pytest.raises(DBAPIError, match="is about a candidate"), connection.begin_nested():
        connection.execute(
            text(
                "INSERT INTO pipeline.review_item (kind, candidate_id, reason_code, proposed_by) "
                "VALUES ('flagged_document', NULL, 'hidden_content', 'x')"
            )
        )


def test_a_cv_read_by_a_reader_no_longer_in_force_is_read_again(intake):
    """The same file is read once (BR-106) — until the reader changes.

    Otherwise, the day the stand-in is swapped for the real service, a candidate uploading the CV
    they sent last week is shown the stand-in's invented answer as though it were their own.
    """
    client, connection, _blobs = intake
    content = cv()
    first = upload(client, content)
    capture = upload_row(connection, first).capture_id
    assert len(read_jobs(connection, capture)) == 1

    # The stand-in answered, and then the real reader came into force.
    _answered(connection, capture, "a-reader-we-no-longer-use")

    again = upload(client, content)
    assert upload_row(connection, again).capture_id == capture  # one file, one capture, still
    assert len(read_jobs(connection, capture)) == 2, (
        "the same bytes are read again by the new reader"
    )


def test_a_cv_already_read_by_the_reader_in_force_is_not_read_twice(intake):
    client, connection, _blobs = intake
    content = cv()
    first = upload(client, content)
    capture = upload_row(connection, first).capture_id
    _answered(connection, capture, "fake-ocr")
    upload(client, content)
    assert len(read_jobs(connection, capture)) == 1


def _answered(connection, capture_id: int, reader: str) -> None:
    """A reading as the worker would have written it: an answer kept as its own capture."""
    answer = connection.execute(
        text(
            "INSERT INTO raw.capture (source, external_id, content_sha256, blob_key, media_type, "
            "byte_size, received_by) VALUES ('ocr_answer', :external, :hash, :key, "
            "'application/json', 12, 'integration-test') RETURNING id"
        ),
        {
            "external": f"answer-{capture_id}-{reader}",
            "hash": hashlib.sha256(f"{capture_id}{reader}".encode()).digest(),
            "key": f"made-up/answer-{capture_id}-{reader}",
        },
    ).scalar_one()
    connection.execute(
        text(
            "INSERT INTO intake.cv_reading (capture_id, outcome, answer_capture_id, reader, "
            "attempts) VALUES (:capture, 'read', :answer, :reader, 1)"
        ),
        {"capture": capture_id, "answer": answer, "reader": reader},
    )
