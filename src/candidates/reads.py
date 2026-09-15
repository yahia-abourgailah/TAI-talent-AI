"""Reading candidates and their evaluations, scoped to the person asking (BR-201, BR-303, BR-408).

A recruiter sees a candidate when they own one of the candidate's applications; a TA lead or an
admin sees every candidate. Every field says where it came from and whether anyone verified it; a
field nobody recorded is not recorded, never a guess (BR-703). Out of scope reads as not found.

Evaluations are read with the same scope, except that the criteria owner reads all of them.
"""

from decimal import Decimal
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Connection

from pipeline.access import Actor, NotFound, run

CANDIDATE_NOT_FOUND = "Candidate not found."
EVALUATION_NOT_FOUND = "Evaluation not found."

_VISIBLE = """
    (:sees_all OR EXISTS (
       SELECT 1 FROM pipeline.application a
       WHERE a.candidate_id = c.id AND a.owner_recruiter = :subject
    ))
"""

_EVALUATION = (
    "e.id, e.candidate_id, e.criteria_version_id, e.origin, e.score, e.tier, e.recommendation, "
    "e.call_priority, e.signals, e.flags, e.flags_text, e.model_version, e.prompt_version, "
    "e.evaluated_at, e.recorded_at"
)


def _visible(conn: Connection, actor: Actor, candidate_id: int) -> dict[str, Any]:
    rows = run(
        conn,
        text(
            f"""
            SELECT c.id, c.created_at, c.archived_at, c.archived_reason, r.source
            FROM core.candidate c JOIN raw.capture r ON r.id = c.capture_id
            WHERE c.id = :id AND {_VISIBLE}
            """
        ),
        {"id": candidate_id, **actor.scope()},
    )
    if not rows:
        raise NotFound(CANDIDATE_NOT_FOUND)
    return rows[0]


def get_candidate(conn: Connection, actor: Actor, candidate_id: int) -> dict[str, Any]:
    candidate = _visible(conn, actor, candidate_id)
    candidate["fields"] = run(
        conn,
        text(
            "SELECT field, value, source, verification_status, verified_at, inference "
            "FROM core.candidate_field_current WHERE candidate_id = :id ORDER BY field"
        ),
        {"id": candidate_id},
    )
    return candidate


def list_candidates(
    conn: Connection,
    actor: Actor,
    *,
    limit: int,
    before: int | None = None,
    opening_id: int | None = None,
    owner: str | None = None,
) -> list[dict[str, Any]]:
    """Newest first. Summaries only: a list carries no candidate fields."""
    return run(
        conn,
        text(
            f"""
            SELECT c.id, c.created_at, c.archived_at, r.source
            FROM core.candidate c JOIN raw.capture r ON r.id = c.capture_id
            WHERE {_VISIBLE}
              AND (CAST(:before AS bigint) IS NULL OR c.id < :before)
              AND (CAST(:opening AS bigint) IS NULL OR EXISTS (
                SELECT 1 FROM pipeline.application o
                WHERE o.candidate_id = c.id AND o.opening_id = :opening
                  AND (:sees_all OR o.owner_recruiter = :subject)
              ))
              AND (CAST(:owner AS text) IS NULL OR EXISTS (
                SELECT 1 FROM pipeline.application w
                WHERE w.candidate_id = c.id AND w.owner_recruiter = :owner
              ))
            ORDER BY c.id DESC LIMIT :limit
            """
        ),
        {"limit": limit, "before": before, "opening": opening_id, "owner": owner, **actor.scope()},
    )


def list_evaluations(conn: Connection, actor: Actor, candidate_id: int) -> list[dict[str, Any]]:
    """Every evaluation of one candidate, newest first."""
    _visible(conn, actor, candidate_id)
    return run(
        conn,
        text(
            f"SELECT {_EVALUATION} FROM core.evaluation e WHERE e.candidate_id = :id "
            "ORDER BY coalesce(e.evaluated_at, e.recorded_at) DESC, e.id DESC"
        ),
        {"id": candidate_id},
    )


def get_evaluation(conn: Connection, actor: Actor, evaluation_id: int) -> dict[str, Any]:
    rows = run(
        conn,
        text(
            f"""
            SELECT {_EVALUATION} FROM core.evaluation e
            JOIN core.candidate c ON c.id = e.candidate_id
            WHERE e.id = :id AND {_VISIBLE}
            """
        ),
        {"id": evaluation_id, **actor.scope()},
    )
    if not rows:
        raise NotFound(EVALUATION_NOT_FOUND)
    return rows[0]


def outcome(evaluation: dict[str, Any]) -> str:
    """failed_gate when a disqualification is recorded, otherwise passed. A failed gate is never a
    rejection: a person confirms it (BR-405)."""
    flags = evaluation.get("flags") or []
    flagged = any(str(flag).startswith("DISQUALIFIED") for flag in flags)
    return (
        "failed_gate"
        if flagged or "DISQUALIFIED" in (evaluation.get("flags_text") or "")
        else "passed"
    )


def track(evaluation: dict[str, Any]) -> str:
    """Track B verdicts carry T tiers (replay.mapping.track)."""
    return "headhunt" if str(evaluation.get("tier") or "").upper().startswith("T") else "entry"


def score(evaluation: dict[str, Any]) -> float | None:
    value = evaluation.get("score")
    return float(value) if isinstance(value, Decimal | int | float) else None
