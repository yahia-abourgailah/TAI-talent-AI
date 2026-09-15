"""Candidates and their evaluations (API plan section 6; BR-201, BR-202, BR-303, BR-307, BR-408).

A candidate's fields each say where they came from and whether anyone verified them. A field
nobody recorded is {"value": null, "state": "not_recorded"}: never a guess. Lists carry summaries
only; fetch one candidate for its fields. Nothing here is ever in a URL but ids.

An evaluation explains a verdict without re-running anything: the criteria version, the outcome,
the score and tier, and the signals and flags behind them. A failed gate is not a rejection.
"""

from collections.abc import Mapping
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy.engine import Connection

from api.deps import current_principal, db_connection
from api.errors import ApiError
from api.fields import Timestamp
from api.ids import decode, decode_filter, encode
from api.pages import DEFAULT_LIMIT, MAX_LIMIT, decode_cursor, next_cursor
from auth import Principal
from candidates import reads
from pipeline.access import actor_from_principal, reader_from_principal

router = APIRouter(prefix="/v1")

Signed = Annotated[Principal, Depends(current_principal)]
Db = Annotated[Connection, Depends(db_connection)]


class CandidateSummaryOut(BaseModel):
    id: str
    source: str
    created_at: Timestamp
    archived: bool


class CandidatePage(BaseModel):
    items: list[CandidateSummaryOut]
    next_cursor: str | None


class CandidateOut(BaseModel):
    id: str
    source: str
    created_at: Timestamp
    archived_at: Timestamp | None
    archived_reason: str | None
    fields: dict[str, dict[str, Any]]


class EvaluationOut(BaseModel):
    id: str
    candidate_id: str
    criteria_version: str
    origin: str
    model_version: str | None
    prompt_version: str | None
    track: str
    outcome: str
    score: float | None
    tier: str | None
    recommendation: str | None
    call_priority: str | None
    signals: list[str]
    flags: list[str]
    evaluated_at: Timestamp | None
    recorded_at: Timestamp


class EvaluationList(BaseModel):
    items: list[EvaluationOut]


def _field(row: Mapping[str, Any]) -> dict[str, Any]:
    if row["verification_status"] == "not_recorded":
        return {"value": None, "state": "not_recorded"}
    shown: dict[str, Any] = {
        "value": row["value"],
        "source": row["source"],
        "verification": row["verification_status"],
        "verified_at": None if row["verified_at"] is None else row["verified_at"].isoformat(),
    }
    if row["inference"] is not None:
        shown["inference"] = row["inference"]
    return shown


def _evaluation(row: dict[str, Any]) -> EvaluationOut:
    return EvaluationOut(
        id=encode("evaluation", row["id"]),
        candidate_id=encode("candidate", row["candidate_id"]),
        criteria_version=row["criteria_version_id"],
        origin=row["origin"],
        model_version=row["model_version"],
        prompt_version=row["prompt_version"],
        track=reads.track(row),
        outcome=reads.outcome(row),
        score=reads.score(row),
        tier=row["tier"],
        recommendation=row["recommendation"],
        call_priority=row["call_priority"],
        signals=list(row["signals"] or []),
        flags=list(row["flags"] or []),
        evaluated_at=row["evaluated_at"],
        recorded_at=row["recorded_at"],
    )


@router.get("/candidates", tags=["candidates"])
def list_candidates(
    principal: Signed,
    conn: Db,
    limit: Annotated[int, Query(ge=1, le=MAX_LIMIT)] = DEFAULT_LIMIT,
    cursor: Annotated[str | None, Query(max_length=200)] = None,
    requisition_id: Annotated[str | None, Query(max_length=40)] = None,
    owner_id: Annotated[str | None, Query(max_length=200)] = None,
) -> CandidatePage:
    """Candidates in your scope, newest first, as summaries."""
    rows = reads.list_candidates(
        conn,
        actor_from_principal(principal),
        limit=limit + 1,
        before=decode_cursor(cursor),
        opening_id=decode_filter("requisition", "requisition_id", requisition_id),
        owner=owner_id,
    )
    items = [
        CandidateSummaryOut(
            id=encode("candidate", row["id"]),
            source=row["source"],
            created_at=row["created_at"],
            archived=row["archived_at"] is not None,
        )
        for row in rows[:limit]
    ]
    return CandidatePage(items=items, next_cursor=next_cursor(rows, limit))


@router.get("/candidates/{candidate_id}", tags=["candidates"])
def get_candidate(candidate_id: str, principal: Signed, conn: Db) -> CandidateOut:
    """One candidate, every field with its source and verification (BR-201, BR-202)."""
    row = reads.get_candidate(
        conn, actor_from_principal(principal), decode("candidate", candidate_id)
    )
    return CandidateOut(
        id=encode("candidate", row["id"]),
        source=row["source"],
        created_at=row["created_at"],
        archived_at=row["archived_at"],
        archived_reason=row["archived_reason"],
        fields={field["field"]: _field(field) for field in row["fields"]},
    )


@router.get("/candidates/{candidate_id}/evaluations", tags=["evaluations"])
def list_evaluations(candidate_id: str, principal: Signed, conn: Db) -> EvaluationList:
    """Every evaluation of a candidate, newest first (BR-303)."""
    actor = reader_from_principal(principal)
    rows = reads.list_evaluations(conn, actor, decode("candidate", candidate_id))
    return EvaluationList(items=[_evaluation(row) for row in rows])


@router.get("/evaluations/{evaluation_id}", tags=["evaluations"])
def get_evaluation(evaluation_id: str, principal: Signed, conn: Db) -> EvaluationOut:
    """One evaluation, with everything needed to explain it (BR-307, CR-04)."""
    actor = reader_from_principal(principal)
    return _evaluation(reads.get_evaluation(conn, actor, decode("evaluation", evaluation_id)))


class CandidateSearchIn(BaseModel):
    full_name: Annotated[str, Field(min_length=2, max_length=200)] | None = None
    email: Annotated[str, Field(min_length=3, max_length=254)] | None = None
    phone: Annotated[str, Field(min_length=10, max_length=40)] | None = None
    limit: int = Field(default=20, ge=1, le=50)


@router.post("/candidates/search", tags=["candidates"])
def search_candidates(body: CandidateSearchIn, principal: Signed, conn: Db) -> CandidatePage:
    """Finds candidates in your scope by full name, email or phone, sent in the body and never in
    the URL. Every value sent must match. Matching is exact once case, spacing and phone formats are
    tidied; it is not a fuzzy or duplicate search. Returns summaries, newest first."""
    actor = actor_from_principal(principal)
    try:
        criteria = reads.SearchCriteria.build(
            full_name=body.full_name, email=body.email, phone=body.phone
        )
    except ValueError as exc:
        raise ApiError(400, "invalid_request", str(exc)) from None
    rows = reads.search_candidates(conn, actor, criteria, limit=body.limit)
    items = [
        CandidateSummaryOut(
            id=encode("candidate", row["id"]),
            source=row["source"],
            created_at=row["created_at"],
            archived=row["archived_at"] is not None,
        )
        for row in rows
    ]
    return CandidatePage(items=items, next_cursor=None)
