"""Scores an application's candidate as soon as the application exists (A1: OBJ-06, BR-301, BR-405).

Creating an application queues a score_application job in the same transaction (migration 0006).
The worker builds the scorer's input from core.candidate_field_current with the replay's own
mapping (replay.mapping.candidate_from_values, no Last Active), so the platform and the replay
cannot disagree on the same fields, and scores with the opening's criteria version.

Every score is a new core.evaluation row, origin 'computed', with evaluated_at: a re-score never
overwrites. A disqualification never rejects anyone. It opens a proposed_rejection review item with
the agreed reason, for a person to confirm. Routing to the other track is not a rejection and opens
nothing. A disqualification with no agreed reason opens nothing and is listed as unresolved.

Nothing written to the run log holds candidate data: ids, codes and counts only. The scorer's own
reason text quotes the candidate's title or location, so it is matched here and never stored.
"""

from collections.abc import Mapping
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Connection

from jobs.queue import ReportableError, RunLog
from pipeline.store import propose_rejection
from replay.mapping import candidate_from_values, input_sha256
from scoring.rulesets.v2026_08_04 import Candidate
from scoring.versions import RULESETS

JOB_KIND = "score_application"
SCORED_BY = "scoring-worker"

# core.candidate_field name -> the workbook column the replay's mapping reads. Last Active is left
# out on purpose, as in the replay (replay/mapping.py).
FIELD_TO_COLUMN: dict[str, str] = {
    "full_name": "Name",
    "age": "Age",
    "current_title": "Title",
    "current_employer": "Employer",
    "location": "Location",
    "phone": "Phone Number",
    "email": "Email",
    "profile_url": "Profile URL",
    "education": "Education",
    "years_experience": "Years Exp",
    "source_platform": "Platform",
}

TRACK_MODE = {"A": "entry", "B": "headhunt"}

# The start of the scorer's disqualify_reason -> the reason code on the review item.
# None: the scorer routes the candidate to the other track, which is not a rejection.
# A reason not listed here is not guessed: no review item, and the run lists it as unresolved.
DISQUALIFICATION_REASONS: tuple[tuple[str, str | None], ...] = (
    ("Current TAI employee", "current_employee"),
    ("Non-Egypt location", "outside_hiring_area"),
    ("Outside Cairo", "outside_hiring_area"),
    ("Over 32", "age_outside_range"),
    ("Under 21", "age_outside_range"),
    ("Over 38", "age_outside_range"),
    ("Managerial/director title", "experience_not_a_fit"),
    ("Mentions 12+ years", "experience_not_a_fit"),
    ("11+ years experience", "experience_not_a_fit"),
    ("Too senior for agent recruit", "experience_not_a_fit"),
    ("Team Leader / Supervisor", None),
    ("Under 25", None),
)


def reason_for(disqualify_reason: str) -> tuple[bool, str | None]:
    """(agreed, reason code). Agreed with no code means routing, not a rejection."""
    for prefix, code in DISQUALIFICATION_REASONS:
        if disqualify_reason.startswith(prefix):
            return True, code
    return False, None


def candidate_from_fields(fields: Mapping[str, str | None]) -> tuple[Candidate, tuple[str, ...]]:
    values = {column: fields.get(field) for field, column in FIELD_TO_COLUMN.items()}
    return candidate_from_values(values)


_APPLICATION = text(
    """
    SELECT a.id, a.candidate_id, o.criteria_version_id, o.track, s.outcome
    FROM pipeline.application a
    JOIN pipeline.opening o ON o.id = a.opening_id
    JOIN pipeline.application_state s ON s.id = a.id
    WHERE a.id = :id
    """
)
_FIELDS = text("SELECT field, value FROM core.candidate_field_current WHERE candidate_id = :id")
_INSERT_EVALUATION = text(
    """
    INSERT INTO core.evaluation
      (candidate_id, criteria_version_id, origin, score, tier, recommendation, signals,
       signals_text, flags, flags_text, evaluated_at, recorded_by)
    VALUES
      (:candidate, :version, 'computed', :score, :tier, :recommendation, :signals,
       :signals_text, :flags, :flags_text, clock_timestamp(), :by)
    RETURNING id
    """
)
_OPEN_REVIEW_ITEM = text(
    """
    SELECT 1 FROM pipeline.review_item r
    WHERE r.application_id = :application AND r.reason_code = :reason
      AND NOT EXISTS (SELECT 1 FROM pipeline.review_resolution x WHERE x.review_item_id = r.id)
    """
)
_REASON_IN_FORCE = text(
    "SELECT 1 FROM pipeline.rejection_reason "
    "WHERE list_version = pipeline.active_list() AND code = :reason"
)


def score_application(conn: Connection, application_id: int, log: RunLog) -> int:
    """Scores the application's candidate, writes the evaluation and returns its id."""
    application = conn.execute(_APPLICATION, {"id": application_id}).one_or_none()
    if application is None:
        raise ReportableError(f"application {application_id} not found")
    ruleset = RULESETS.get(application.criteria_version_id)
    if ruleset is None:
        raise ReportableError(f"no scorer for criteria version {application.criteria_version_id}")

    fields = {
        row.field: row.value for row in conn.execute(_FIELDS, {"id": application.candidate_id})
    }
    candidate, unreadable = candidate_from_fields(fields)
    for column in unreadable:
        log.unresolve("unreadable_number", application_id=application_id, column=column)
    log.input_sha256 = input_sha256(candidate)

    result = ruleset.score_candidate(candidate, mode=TRACK_MODE[application.track])
    signals = sorted(result.key_signals)
    flags = sorted(result.red_flags)
    evaluation_id = int(
        conn.execute(
            _INSERT_EVALUATION,
            {
                "candidate": application.candidate_id,
                "version": application.criteria_version_id,
                "score": result.overall_score,
                "tier": result.priority or None,
                "recommendation": result.recommendation or None,
                "signals": signals,
                "signals_text": "; ".join(signals) or None,
                "flags": flags,
                "flags_text": "; ".join(flags) or None,
                "by": SCORED_BY,
            },
        ).scalar_one()
    )
    log.count("evaluations_computed")

    if result.disqualified:
        _review_disqualification(conn, application, result.disqualify_reason, evaluation_id, log)
    return evaluation_id


def _review_disqualification(
    conn: Connection, application: Any, disqualify_reason: str, evaluation_id: int, log: RunLog
) -> None:
    agreed, reason = reason_for(disqualify_reason)
    detail = {"application_id": application.id, "evaluation_id": evaluation_id}
    if not agreed:
        log.unresolve("disqualification_without_an_agreed_reason", **detail)
    elif reason is None:
        log.count("routed_to_other_track")
    elif application.outcome is not None:
        log.count("already_final_no_review_item")
    elif conn.execute(_OPEN_REVIEW_ITEM, {"application": application.id, "reason": reason}).first():
        log.count("review_item_already_open")
    elif not conn.execute(_REASON_IN_FORCE, {"reason": reason}).first():
        log.unresolve("reason_not_on_the_list_in_force", reason_code=reason, **detail)
    else:
        propose_rejection(conn, application.id, reason, SCORED_BY)
        log.count("review_items_opened")


def handle(conn: Connection, params: Mapping[str, Any], actor: str, log: RunLog) -> None:
    """The job handler for score_application."""
    try:
        application_id = int(params["application_id"])
    except (KeyError, TypeError, ValueError):
        raise ReportableError("score_application needs an application_id") from None
    score_application(conn, application_id, log)
