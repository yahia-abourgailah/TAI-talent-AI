"""Reading a CV, as a background job (B3: NFR-01, NFR-04, BR-308; A1 for the fields).

    read_cv {capture_id, attempt}

  1. Wait for a free reading slot: at most OcrLimits.max_concurrency CVs are read at once, across
     every worker. With none free, the job is queued again a few seconds later.
  2. Send the kept file to the OCR, through the one adapter (intake.ocr).
  3. Save the answer exactly as it came, as its own raw capture (source ocr_answer).
  4. Turn it into fields (intake.cv_fields): source cv_extraction, unverified.
  5. Record the reading. If the OCR found hidden content, open a flagged_document review item,
     whatever the score. The candidate is never told.

A timeout or an unavailable OCR is tried again with growing gaps, up to max_attempts. When it still
fails, when the OCR refuses the file, or when its answer is not readable, the reading is recorded
as failed and a flagged_document review item opens: the CV waits for a person, and is never lost.

A worker that stops mid-read keeps nothing from that attempt; the job is queued again
(jobs.queue.recover_stopped). If it can no longer be run, settle_abandoned records the reading as
failed and opens the review item.

Nothing written to the run log holds CV content: ids, codes and counts only.
"""

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from functools import lru_cache
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Connection

from config import get_settings
from importer.blobs import BlobStore, s3_store
from intake.answer import AnswerUnreadable, parse_answer
from intake.cv_fields import SOURCE as FIELD_SOURCE
from intake.cv_fields import map_answer
from intake.ocr import OcrError, OcrLimits, OcrReader, reader_from_settings
from intake.records import FieldRow, add_fields, keep_original, open_document_review
from jobs.queue import ReportableError, RunLog, enqueue

JOB_KIND = "read_cv"
READ_BY = "cv-reader"
ANSWER_SOURCE = "ocr_answer"
_SLOT_NAMESPACE = 7304

HIDDEN_CONTENT = "hidden_content"
ANSWER_UNREADABLE = "ocr_answer_unreadable"
DOCUMENT_MISSING = "document_missing"
ABANDONED = "ocr_failed"


def _today() -> date:
    return datetime.now(UTC).date()


@dataclass(frozen=True)
class Services:
    blobs: BlobStore
    reader: OcrReader
    limits: OcrLimits = field(default_factory=OcrLimits)
    today: Callable[[], date] = _today


@lru_cache
def services() -> Services:
    """Built from settings once per process. Fails at startup on a misconfigured OCR."""
    settings = get_settings()
    return Services(
        blobs=s3_store(settings),
        reader=reader_from_settings(settings),
        limits=OcrLimits.from_settings(settings),
    )


def reading_ref(reading_id: int) -> str:
    return f"intake.cv_reading:{reading_id}"


@dataclass(frozen=True, slots=True)
class Document:
    capture_id: int
    candidate_id: int
    blob_key: str
    media_type: str


_DOCUMENT = text(
    """
    SELECT r.id, r.source, r.blob_key, r.media_type,
           (SELECT u.candidate_id FROM intake.cv_upload u
            WHERE u.capture_id = r.id ORDER BY u.id LIMIT 1) AS candidate_id
    FROM raw.capture r WHERE r.id = :id
    """
)
_ALREADY_READ = text("SELECT 1 FROM intake.cv_reading WHERE capture_id = :id")
_RECORD = text(
    """
    INSERT INTO intake.cv_reading
      (capture_id, outcome, failure, hidden_content, answer_capture_id, reader, attempts, job_id)
    VALUES
      (:capture, :outcome, :failure, :hidden, :answer, :reader, :attempts,
       (SELECT id FROM jobs.job WHERE kind = 'read_cv' AND status = 'running'
          AND params ->> 'capture_id' = CAST(:capture AS text)
        ORDER BY id DESC LIMIT 1))
    ON CONFLICT (capture_id) DO NOTHING
    RETURNING id
    """
)


def _document(conn: Connection, capture_id: int) -> Document:
    row = conn.execute(_DOCUMENT, {"id": capture_id}).one_or_none()
    if row is None or row.source != "cv_upload" or row.candidate_id is None:
        raise ReportableError(f"capture {capture_id} is not an uploaded CV")
    return Document(capture_id, int(row.candidate_id), row.blob_key, row.media_type)


def _take_slot(conn: Connection, slots: int) -> bool:
    """A reading slot, held until this transaction ends: by commit, rollback or a dead worker."""
    for slot in range(slots):
        taken = conn.execute(
            text("SELECT pg_try_advisory_xact_lock(:namespace, :slot)"),
            {"namespace": _SLOT_NAMESPACE, "slot": slot},
        ).scalar_one()
        if taken:
            return True
    return False


def record_failure(
    conn: Connection,
    document: Document,
    code: str,
    *,
    reader: str,
    attempts: int,
    log: RunLog,
    answer_capture_id: int | None = None,
) -> None:
    """The reading failed: recorded once, and the CV goes to a person."""
    recorded = conn.execute(
        _RECORD,
        {
            "capture": document.capture_id,
            "outcome": "failed",
            "failure": code,
            "hidden": False,
            "answer": answer_capture_id,
            "reader": reader,
            "attempts": attempts,
        },
    ).scalar_one_or_none()
    if recorded is None:
        log.count("already_recorded")
        return
    log.count(f"failed_{code}")
    if open_document_review(conn, document.candidate_id, document.capture_id, code, READ_BY):
        log.count("review_items_opened")


def read_cv(
    conn: Connection,
    capture_id: int,
    attempt: int,
    log: RunLog,
    svc: Services,
    requested_by: str = READ_BY,
) -> None:
    document = _document(conn, capture_id)
    if conn.execute(_ALREADY_READ, {"id": capture_id}).first():
        log.count("already_read")
        return
    reader = svc.reader.name
    limits = svc.limits

    if not _take_slot(conn, limits.max_concurrency):
        enqueue(
            conn,
            JOB_KIND,
            {"capture_id": capture_id, "attempt": attempt},
            requested_by,
            delay_seconds=limits.busy_wait_seconds,
        )
        log.count("waited_for_a_reading_slot")
        return

    content = svc.blobs.get(document.blob_key)
    if content is None:
        log.unresolve(DOCUMENT_MISSING, capture_id=capture_id)
        record_failure(conn, document, DOCUMENT_MISSING, reader=reader, attempts=attempt, log=log)
        return

    try:
        reply = svc.reader.read(content, document.media_type)
    except OcrError as exc:
        log.count(exc.code)
        if exc.retryable and attempt < limits.max_attempts:
            next_attempt = attempt + 1
            enqueue(
                conn,
                JOB_KIND,
                {"capture_id": capture_id, "attempt": next_attempt},
                requested_by,
                delay_seconds=limits.gap_before(next_attempt),
            )
            log.count("retries_queued")
            return
        record_failure(conn, document, exc.code, reader=reader, attempts=attempt, log=log)
        return

    answer = keep_original(
        conn,
        svc.blobs,
        source=ANSWER_SOURCE,
        external_id=f"cv:{capture_id}",
        content=reply.body,
        media_type="application/json",
        extension="json",
        received_by=reader,
    )
    log.count("answers_kept")
    try:
        parsed = parse_answer(reply.body)
    except AnswerUnreadable as exc:
        log.unresolve(ANSWER_UNREADABLE, capture_id=capture_id, problem=str(exc))
        record_failure(
            conn,
            document,
            ANSWER_UNREADABLE,
            reader=reader,
            attempts=attempt,
            log=log,
            answer_capture_id=answer.id,
        )
        return

    mapped = map_answer(parsed, svc.today())
    reading_id = conn.execute(
        _RECORD,
        {
            "capture": capture_id,
            "outcome": "read",
            "failure": None,
            "hidden": mapped.hidden_content,
            "answer": answer.id,
            "reader": reader,
            "attempts": attempt,
        },
    ).scalar_one_or_none()
    if reading_id is None:
        log.count("already_read")
        return

    rows = [FieldRow(f.field, f.value, f.status, f.inference, f.language) for f in mapped.fields]
    written = add_fields(
        conn,
        document.candidate_id,
        rows,
        source=FIELD_SOURCE,
        source_ref=reading_ref(int(reading_id)),
        recorded_by=READ_BY,
    )
    log.count("cvs_read")
    log.count("fields_written", written)
    log.count("fields_not_recorded", sum(1 for f in mapped.fields if f.value is None))
    for name in mapped.unreadable:
        log.unresolve("unreadable_value", capture_id=capture_id, field=name)
    if mapped.ignored_fields:
        log.count("answer_fields_ignored", mapped.ignored_fields)

    if mapped.hidden_content:
        log.count("hidden_content_found")
        if open_document_review(conn, document.candidate_id, capture_id, HIDDEN_CONTENT, READ_BY):
            log.count("review_items_opened")


def handle(conn: Connection, params: Mapping[str, Any], actor: str, log: RunLog) -> None:
    """The job handler for read_cv."""
    try:
        capture_id = int(params["capture_id"])
        attempt = int(params.get("attempt", 1))
    except (KeyError, TypeError, ValueError):
        raise ReportableError("read_cv needs a capture_id") from None
    read_cv(conn, capture_id, attempt, log, services(), actor)


_ABANDONED = text(
    """
    SELECT u.capture_id,
           (SELECT count(*) FROM jobs.job j WHERE j.kind = 'read_cv'
              AND j.params ->> 'capture_id' = CAST(u.capture_id AS text)) AS jobs
    FROM (SELECT DISTINCT capture_id FROM intake.cv_upload) u
    WHERE NOT EXISTS (SELECT 1 FROM intake.cv_reading r WHERE r.capture_id = u.capture_id)
      AND NOT EXISTS (
        SELECT 1 FROM jobs.job j
        WHERE j.kind = 'read_cv' AND j.status IN ('queued', 'running')
          AND j.params ->> 'capture_id' = CAST(u.capture_id AS text)
      )
    ORDER BY u.capture_id
    """
)


def settle_abandoned(conn: Connection) -> dict[str, int]:
    """Every uploaded CV with no reading and no job left to read it: recorded as failed, and sent
    to a person. Run by the worker after it recovers stopped jobs."""
    log = RunLog()
    for row in conn.execute(_ABANDONED).all():
        document = _document(conn, int(row.capture_id))
        record_failure(conn, document, ABANDONED, reader=READ_BY, attempts=int(row.jobs), log=log)
    return log.counts
