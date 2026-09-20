"""Reading one application's CV against its job (BR-305, BR-306, CR-05).

    application to a job of kind `other`  ->  assess_application  ->  an opinion, and a person

What is written: one core.evaluation with origin 'ai', the score the model gave, the model and the
prompt that produced it, its reasons as signals and what the job asks for and the CV lacks as
flags. **No tier, no call priority** — the database refuses them for an AI evaluation (migration
0018), so this cannot move anybody through the pipeline even by mistake.

Then a review item, because an assessment is something a person reads and acts on, not a decision
(CR-05). Nothing is rejected here, ever.

If the model is busy or unreachable the job waits and tries again; if it answers something we
cannot check against the CV, the CV goes to a person with no score attached. A CV is never lost and
never judged by silence.
"""

from collections.abc import Mapping
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Connection

from assess.answer import PROMPT_VERSION, Assessment, AssessmentUnreadable, parse
from assess.model import Model, ModelRefused, ModelUnavailable
from assess.prompt import SYSTEM, cv_text, question
from config import Settings, get_settings
from jobs.queue import ReportableError, RunLog, enqueue
from pipeline.store import open_ai_review_item

JOB_KIND = "assess_application"
ASSESSED_BY = "assessment-worker"
RETRY_GAPS_SECONDS = (60, 300, 1800)
MAX_ATTEMPTS = 4

_APPLICATION = text(
    """
    SELECT a.id, a.candidate_id, o.id AS opening_id, o.job_type, o.title, o.description,
           s.outcome
    FROM pipeline.application a
    JOIN pipeline.opening o ON o.id = a.opening_id
    JOIN pipeline.application_state s ON s.id = a.id
    WHERE a.id = :id
    """
)
_FIELDS = text("SELECT field, value FROM core.candidate_field_current WHERE candidate_id = :id")
_CV_TEXT = text(
    """
    SELECT f.value FROM core.candidate_field_current f
    WHERE f.candidate_id = :id AND f.field = 'cv_text' AND f.value IS NOT NULL
    """
)
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


def model_from_settings(settings: Settings) -> Model:
    if not settings.vllm_base_url:
        raise ReportableError(
            "TALENT_VLLM_BASE_URL is not set: a job that is not sales cannot be assessed."
        )
    return Model(
        settings.vllm_base_url,
        settings.vllm_model,
        api_key=settings.vllm_api_key.get_secret_value(),
        timeout_seconds=settings.vllm_timeout_seconds,
    )


def _values(conn: Connection, candidate_id: int) -> dict[str, str | None]:
    return {row.field: row.value for row in conn.execute(_FIELDS, {"id": candidate_id})}


def record(
    conn: Connection,
    *,
    application: Any,
    found: Assessment,
    model: str,
    criteria_version: str,
) -> int:
    """Keeps the assessment as an evaluation, and opens the item a person works from."""
    signals = found.as_signals()
    flags = found.as_flags()
    evaluation_id = int(
        conn.execute(
            _RECORD,
            {
                "candidate": application.candidate_id,
                "application": application.id,
                "criteria": criteria_version,
                "score": found.score,
                "summary": found.summary or None,
                "signals": signals,
                "signals_text": "; ".join(signals) or None,
                "flags": flags,
                "flags_text": "; ".join(flags) or None,
                "model": model,
                "prompt": PROMPT_VERSION,
                "by": ASSESSED_BY,
            },
        ).scalar_one()
    )
    open_ai_review_item(conn, application.id, evaluation_id, ASSESSED_BY)
    return evaluation_id


def assess_application(
    conn: Connection, application_id: int, log: RunLog, settings: Settings | None = None
) -> int | None:
    """Reads the CV against the job, and returns the evaluation's id.

    None when nothing was written: the job is not one of these, or there was nothing to read.
    """
    settings = settings or get_settings()
    application = conn.execute(_APPLICATION, {"id": application_id}).one_or_none()
    if application is None:
        raise ReportableError(f"application {application_id} not found")
    if application.job_type != "other":
        log.count("not_an_other_job")
        return None
    if not (application.description or "").strip():
        raise ReportableError(f"opening {application.opening_id} has no description to judge by")

    fields = _values(conn, int(application.candidate_id))
    written = conn.execute(_CV_TEXT, {"id": application.candidate_id}).scalar_one_or_none() or ""
    shown = cv_text(fields, str(written))
    if not shown.strip():
        # Nothing to read. A person looks at the file itself; no score is invented for an empty CV.
        open_ai_review_item(conn, application.id, None, ASSESSED_BY)
        log.count("nothing_to_read")
        log.count("review_items_opened")
        return None

    model = model_from_settings(settings)
    try:
        reply = model.ask(
            SYSTEM,
            question(str(application.title or "the role"), str(application.description), shown),
        )
    finally:
        model.close()

    found = parse(reply.text, shown)
    log.count("assessments")
    log.count("quotes_found", found.quotes_found)
    if found.quotes_checked > found.quotes_found:
        log.skip("reasons_without_a_quote_in_the_cv", found.quotes_checked - found.quotes_found)

    criteria_version = str(settings_criteria(conn, int(application.opening_id)))
    evaluation_id = record(
        conn,
        application=application,
        found=found,
        model=reply.model,
        criteria_version=criteria_version,
    )
    log.count("review_items_opened")
    return evaluation_id


def settings_criteria(conn: Connection, opening_id: int) -> str:
    return str(
        conn.execute(
            text("SELECT criteria_version_id FROM pipeline.opening WHERE id = :id"),
            {"id": opening_id},
        ).scalar_one()
    )


def handle(conn: Connection, params: Mapping[str, Any], actor: str, log: RunLog) -> None:
    """The job handler. A model that is busy or down is waited for, not given up on."""
    try:
        application_id = int(params["application_id"])
    except (KeyError, TypeError, ValueError):
        raise ReportableError("assess_application needs an application_id") from None
    attempt = int(params.get("attempt") or 1)

    try:
        assess_application(conn, application_id, log)
    except ModelUnavailable as waiting:
        if attempt >= MAX_ATTEMPTS:
            open_ai_review_item(conn, application_id, None, ASSESSED_BY)
            log.count("gave_up_model_unavailable")
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
        log.count("model_unavailable")
        log.skip(str(waiting).split(".")[0].lower().replace(" ", "_")[:40])
    except (ModelRefused, AssessmentUnreadable) as refused:
        # The CV is fine; the answer was not. A person reads the CV with nothing attached.
        open_ai_review_item(conn, application_id, None, ASSESSED_BY)
        log.count("answer_not_usable")
        log.count("review_items_opened")
        log.unresolve(
            "assessment_answer_not_usable",
            application_id=application_id,
            because=str(refused)[:200],
        )
