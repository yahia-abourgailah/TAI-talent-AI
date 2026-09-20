"""Matching one application's CV against what its job asks for (BR-305, BR-306, CR-05).

    application to a job of kind `other`  ->  assess_application  ->  a percentage, and a person

The criteria version scores a sales hire. A job that is not sales lists the skills it asks for and
the level it wants of each (migration 0020), and the candidate's own CV file is sent to the company
CV service, which extracts it and grades it against that list in code.

What is written: one core.evaluation with origin 'ai', the percentage the service returned, the
skills it found with the CV's own evidence as signals, and the skills it did not find as flags.
**No tier, no call priority** — the database refuses them for an AI evaluation (migration 0018),
so this cannot move anybody through the pipeline even by mistake.

Then a review item, because a percentage is something a person reads and acts on, not a decision
(CR-05). Nothing is rejected here, ever.

If the service is busy or unreachable the job waits and tries again; if it answers something we
cannot tie to the skills asked for, and when the candidate has no CV file at all, the application
goes to a person with no score attached. A CV is never lost and never judged by silence.
"""

from collections.abc import Mapping
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Connection

from assess.match import MATCHER_VERSION, Matcher, MatchReport, MatchUnreadable, SkillMatcher
from assess.match import requirements_payload as payload
from config import OcrMode, Settings, get_settings
from importer.blobs import BlobStore
from intake.ocr import OcrRejected, OcrTimeout, OcrUnavailable
from jobs.queue import ReportableError, RunLog, enqueue
from pipeline.store import open_ai_review_item

JOB_KIND = "assess_application"
ASSESSED_BY = "assessment-worker"
RETRY_GAPS_SECONDS = (60, 300, 1800)
MAX_ATTEMPTS = 4

_APPLICATION = text(
    """
    SELECT a.id, a.candidate_id, o.id AS opening_id, o.job_type, o.title, o.requirements
    FROM pipeline.application a
    JOIN pipeline.opening o ON o.id = a.opening_id
    WHERE a.id = :id
    """
)
# The newest CV file the candidate has given us. A re-upload is read against the job, not the file
# it replaced.
_CV_FILE = text(
    """
    SELECT r.id, r.blob_key, r.media_type
    FROM raw.capture r
    JOIN intake.cv_upload u ON u.capture_id = r.id
    WHERE u.candidate_id = :candidate
    ORDER BY r.id DESC LIMIT 1
    """
)
_CRITERIA = text("SELECT criteria_version_id FROM pipeline.opening WHERE id = :id")
_RECORD = text(
    """
    INSERT INTO core.evaluation
      (candidate_id, application_id, criteria_version_id, origin, score, recommendation,
       signals, signals_text, flags, flags_text, model_version, prompt_version, evaluated_at,
       recorded_by)
    VALUES
      (:candidate, :application, :criteria, 'ai', :score, :summary, :signals, :signals_text,
       :flags, :flags_text, :model, :prompt, clock_timestamp(), :by)
    RETURNING id
    """
)


def matcher_from_settings(settings: Settings) -> SkillMatcher:
    """The CV service, or its stand-in on a dev machine (the same switch the CV reader uses)."""
    if settings.ocr_mode_in_force is OcrMode.FAKE:
        from assess.fake_match import FakeMatcher

        return FakeMatcher()
    if not settings.ocr_base_url:
        raise ReportableError(
            "TALENT_OCR_BASE_URL is not set: a job that is not sales cannot be matched."
        )
    return Matcher(
        settings.ocr_base_url,
        api_key=settings.ocr_api_key.get_secret_value(),
        timeout_seconds=max(settings.ocr_timeout_seconds, 120.0),
    )


def blobs_from_settings(settings: Settings) -> BlobStore:
    from importer.blobs import s3_store

    return s3_store(settings)


def summary(report: MatchReport) -> str:
    met = len(report.met)
    return f"{met} of the {len(report.lines)} skills this job asks for are shown in the CV."


def record(
    conn: Connection,
    *,
    application: Any,
    report: MatchReport,
    criteria_version: str,
) -> int:
    """Keeps the match as an evaluation, and opens the item a person works from."""
    signals = [line.as_text() for line in report.met]
    flags = [line.as_text() for line in report.unmet]
    stamp = MATCHER_VERSION
    if report.requirements_hash:
        stamp = f"{MATCHER_VERSION}+{report.requirements_hash}"
    evaluation_id = int(
        conn.execute(
            _RECORD,
            {
                "candidate": application.candidate_id,
                "application": application.id,
                "criteria": criteria_version,
                "score": report.percentage,
                "summary": summary(report),
                "signals": signals,
                "signals_text": "; ".join(signals) or None,
                "flags": flags,
                "flags_text": "; ".join(flags) or None,
                "model": f"cv-service/{report.method}" if report.method else "cv-service",
                "prompt": stamp,
                "by": ASSESSED_BY,
            },
        ).scalar_one()
    )
    open_ai_review_item(conn, application.id, evaluation_id, ASSESSED_BY)
    return evaluation_id


def assess_application(
    conn: Connection,
    application_id: int,
    log: RunLog,
    settings: Settings | None = None,
    blobs: BlobStore | None = None,
) -> int | None:
    """Matches the CV against the job's skills, and returns the evaluation's id.

    None when nothing was written: the job is not one of these, or there is no CV file to send.
    """
    settings = settings or get_settings()
    application = conn.execute(_APPLICATION, {"id": application_id}).one_or_none()
    if application is None:
        raise ReportableError(f"application {application_id} not found")
    if application.job_type != "other":
        log.count("not_an_other_job")
        return None
    wanted = list(application.requirements or [])
    if not wanted:
        raise ReportableError(f"opening {application.opening_id} asks for no skills")

    found = conn.execute(_CV_FILE, {"candidate": application.candidate_id}).one_or_none()
    content = None
    if found is not None:
        store = blobs or blobs_from_settings(settings)
        content = store.get(str(found.blob_key))
    if found is None or not content:
        # Nothing to send. A person looks at the record itself; no percentage is invented for a
        # candidate whose CV we never had (BR-703).
        open_ai_review_item(conn, application.id, None, ASSESSED_BY)
        log.count("no_cv_file" if found is None else "cv_file_missing")
        log.count("review_items_opened")
        return None

    matcher = matcher_from_settings(settings)
    try:
        report = matcher.match(
            content,
            str(found.media_type),
            payload(str(application.title or "the role"), wanted),
        )
    finally:
        matcher.close()

    log.count("matches")
    log.count("skills_asked_for", len(report.lines))
    log.count("skills_shown", len(report.met))
    criteria_version = str(conn.execute(_CRITERIA, {"id": application.opening_id}).scalar_one())
    evaluation_id = record(
        conn, application=application, report=report, criteria_version=criteria_version
    )
    log.count("review_items_opened")
    return evaluation_id


def handle(conn: Connection, params: Mapping[str, Any], actor: str, log: RunLog) -> None:
    """The job handler. A service that is busy or down is waited for, not given up on."""
    try:
        application_id = int(params["application_id"])
    except (KeyError, TypeError, ValueError):
        raise ReportableError("assess_application needs an application_id") from None
    attempt = int(params.get("attempt") or 1)

    try:
        assess_application(conn, application_id, log)
    except (OcrUnavailable, OcrTimeout) as waiting:
        if attempt >= MAX_ATTEMPTS:
            open_ai_review_item(conn, application_id, None, ASSESSED_BY)
            log.count("gave_up_service_unavailable")
            log.count("review_items_opened")
            return
        gap = RETRY_GAPS_SECONDS[min(attempt - 1, len(RETRY_GAPS_SECONDS) - 1)]
        enqueue(
            conn,
            JOB_KIND,
            {"application_id": application_id, "attempt": attempt + 1},
            ASSESSED_BY,
            delay_seconds=gap,
        )
        log.count("service_unavailable")
        log.skip(str(waiting).split(".")[0].lower().replace(" ", "_")[:40])
    except (OcrRejected, MatchUnreadable) as refused:
        # The application is fine; the answer was not. A person reads the CV with nothing attached.
        open_ai_review_item(conn, application_id, None, ASSESSED_BY)
        log.count("answer_not_usable")
        log.count("review_items_opened")
        log.unresolve(
            "match_answer_not_usable",
            application_id=application_id,
            because=str(refused)[:200],
        )
