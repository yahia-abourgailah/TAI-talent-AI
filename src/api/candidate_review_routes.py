"""Review items about a candidate (API plan section 6; BR-407, BR-308, BR-202).

    flagged_document      a CV a person must look at: hidden content was found, or reading it
                          failed. reason_code says which.
    unverified_candidate  a candidate typed in by hand and not yet checked.
    possible_duplicate    two records that may be one person.
    borderline_score      a score close to a tier line (BR-310): the item names the evaluation and
                          the tiers either side. The candidate keeps the tier the score gives.

Every item carries `reason`, a sentence a recruiter reads, next to the stable `reason_code`.

These have no application, so they are served here rather than on /v1/review-items, whose frozen
items always carry one. Both lists share the rvw_ ids. A caller must handle a kind or reason code
it does not know: more arrive in week 6 (possible duplicates).

Resolve an item as checked (a person looked at the file, or checked the candidate), or dismiss it
with a reason. An unverified candidate is checked once none of its fields is left unchecked;
checking the last field resolves it by itself.
"""

from collections.abc import Mapping
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy.engine import Connection

from api import idempotency
from api.deps import current_principal, db_connection
from api.errors import ApiError
from api.fields import Timestamp
from api.ids import decode, decode_filter, encode
from api.pages import DEFAULT_LIMIT, MAX_LIMIT, decode_cursor, next_cursor
from auth import Principal
from candidates import joins, reviews
from candidates.queue import reason_text
from pipeline.access import Refused, actor_from_principal, reader_from_principal

router = APIRouter(prefix="/v1", tags=["review queue"])

Signed = Annotated[Principal, Depends(current_principal)]
Db = Annotated[Connection, Depends(db_connection)]

DECISION_OUT = {"checked": "checked", "dismissed": "dismissed"}
DECISION_IN = {"checked": "checked", "dismiss": "dismissed"}


class CandidateResolutionOut(BaseModel):
    decision: str
    reason: str | None
    resolved_by: str
    resolved_at: Timestamp


class MatchOut(BaseModel):
    other_candidate_id: str
    strength: str
    evidence: list[str]


class BorderlineOut(BaseModel):
    evaluation_id: str
    tier_above: str
    tier_below: str


class AssessmentOut(BaseModel):
    """A CV read against one job by the model (BR-305). No tier: an AI never sets one (BR-306)."""

    evaluation_id: str
    application_id: str | None
    score: int | None


class CandidateReviewItemOut(BaseModel):
    id: str
    kind: str
    candidate_id: str
    document_id: str | None
    match: MatchOut | None
    reason_code: str
    reason: str
    borderline: BorderlineOut | None = None
    assessment: AssessmentOut | None = None
    proposed_by: str
    proposed_at: Timestamp
    resolution: CandidateResolutionOut | None


class CandidateReviewItemPage(BaseModel):
    items: list[CandidateReviewItemOut]
    next_cursor: str | None


class CandidateResolutionIn(BaseModel):
    decision: Literal["checked", "dismiss"]
    reason: Annotated[str, Field(min_length=1, max_length=500, pattern=r"\S")] | None = None


def _item(row: Mapping[str, Any]) -> CandidateReviewItemOut:
    resolution = None
    if row["resolution"] is not None:
        resolution = CandidateResolutionOut(
            decision=DECISION_OUT.get(row["resolution"], row["resolution"]),
            reason=row["resolution_reason"],
            resolved_by=row["resolved_by"],
            resolved_at=row["resolved_at"],
        )
    capture = row["capture_id"]
    match = None
    if row.get("match_id") is not None:
        match = MatchOut(
            other_candidate_id=encode("candidate", row["match_other_candidate_id"]),
            strength=row["match_strength"],
            evidence=list(row["match_evidence"] or []),
        )
    borderline = None
    if row["kind"] == "borderline_score" and row.get("evaluation_id") is not None:
        borderline = BorderlineOut(
            evaluation_id=encode("evaluation", row["evaluation_id"]),
            tier_above=row["tier_above"],
            tier_below=row["tier_below"],
        )
    assessment = None
    if row["kind"] == "ai_assessment" and row.get("evaluation_id") is not None:
        assessed = row.get("assessed_application_id")
        assessment = AssessmentOut(
            evaluation_id=encode("evaluation", row["evaluation_id"]),
            application_id=None if assessed is None else encode("application", assessed),
            score=None if row.get("assessed_score") is None else int(row["assessed_score"]),
        )
    return CandidateReviewItemOut(
        id=encode("review_item", row["id"]),
        kind=row["kind"],
        candidate_id=encode("candidate", row["candidate_id"]),
        document_id=None if capture is None else encode("document", capture),
        match=match,
        reason_code=row["reason_code"],
        reason=reason_text(dict(row)),
        borderline=borderline,
        assessment=assessment,
        proposed_by=row["proposed_by"],
        proposed_at=row["proposed_at"],
        resolution=resolution,
    )


@router.get("/candidate-review-items")
def list_candidate_review_items(
    principal: Signed,
    conn: Db,
    limit: Annotated[int, Query(ge=1, le=MAX_LIMIT)] = DEFAULT_LIMIT,
    cursor: Annotated[str | None, Query(max_length=200)] = None,
    status: Annotated[Literal["open", "resolved"], Query()] = "open",
    kind: Annotated[
        Literal[
            "flagged_document",
            "unverified_candidate",
            "possible_duplicate",
            "borderline_score",
            "ai_assessment",
        ]
        | None,
        Query(),
    ] = None,
    candidate_id: Annotated[str | None, Query(max_length=40)] = None,
) -> CandidateReviewItemPage:
    """Items about candidates in your scope, newest first. Open items by default."""
    rows = reviews.list_items(
        conn,
        reader_from_principal(principal),
        limit=limit + 1,
        before=decode_cursor(cursor),
        status=status,
        kind=kind,
        candidate_id=decode_filter("candidate", "candidate_id", candidate_id),
    )
    return CandidateReviewItemPage(
        items=[_item(row) for row in rows[:limit]], next_cursor=next_cursor(rows, limit)
    )


@router.get("/candidate-review-items/{review_item_id}")
def get_candidate_review_item(
    review_item_id: str, principal: Signed, conn: Db
) -> CandidateReviewItemOut:
    actor = reader_from_principal(principal)
    return _item(reviews.get_item(conn, actor, decode("review_item", review_item_id)))


@router.post(
    "/candidate-review-items/{review_item_id}/resolution",
    status_code=201,
    response_model=CandidateReviewItemOut,
)
def resolve_candidate_review_item(
    review_item_id: str,
    body: CandidateResolutionIn,
    request: Request,
    principal: Signed,
    conn: Db,
    key: idempotency.KeyHeader = None,
) -> JSONResponse:
    """checked: you looked at the file, or checked the candidate (every field). dismiss: needs a
    reason. Recorded with your name, once."""
    actor = actor_from_principal(principal)
    number = decode("review_item", review_item_id)

    def resolve() -> CandidateReviewItemOut:
        if body.decision == "dismiss" and body.reason is None:
            raise ApiError(400, "invalid_request", "Dismissing a review item needs a reason.")
        row = reviews.resolve(conn, actor, number, DECISION_IN[body.decision], body.reason)
        return _item(row)

    return idempotency.respond(conn, request, principal.subject, key, body, 201, resolve)


# --- Two records, one person (BR-203, BR-204, BR-206) ---------------------------------------------


class JoinIn(BaseModel):
    joined_candidate_id: Annotated[str, Field(max_length=40)]
    reason: Annotated[str, Field(min_length=1, max_length=500, pattern=r"\S")]


class UndoJoinIn(BaseModel):
    reason: Annotated[str, Field(min_length=1, max_length=500, pattern=r"\S")]


class JoinOut(BaseModel):
    id: str
    primary_candidate_id: str
    joined_candidate_id: str
    reason: str
    joined_by: str
    joined_at: Timestamp
    undone_at: Timestamp | None
    undone_by: str | None
    undone_reason: str | None


class GroupOut(BaseModel):
    primary_candidate_id: str
    members: list[str]


def _join(row: Mapping[str, Any]) -> JoinOut:
    return JoinOut(
        id=encode("join", row["id"]),
        primary_candidate_id=encode("candidate", row["primary_id"]),
        joined_candidate_id=encode("candidate", row["joined_id"]),
        reason=row["reason"],
        joined_by=row["joined_by"],
        joined_at=row["joined_at"],
        undone_at=row["undone_at"],
        undone_by=row["undone_by"],
        undone_reason=row["undone_reason"],
    )


@router.post(
    "/candidates/{candidate_id}/joins", status_code=201, response_model=JoinOut, tags=["candidates"]
)
def join_candidates(
    candidate_id: str,
    body: JoinIn,
    request: Request,
    principal: Signed,
    conn: Db,
    key: idempotency.KeyHeader = None,
) -> JSONResponse:
    """Records that two records are one person, with your name and a reason. Both records stay as
    they are: the joined one is read under this one, and undoing puts it back."""
    actor = actor_from_principal(principal)
    primary = decode("candidate", candidate_id)
    joined = decode("candidate", body.joined_candidate_id)

    def decide() -> JoinOut:
        try:
            return _join(joins.join(conn, actor, primary, joined, body.reason))
        except Refused as refusal:
            if refusal.code == "invalid_request":
                raise ApiError(400, refusal.code, str(refusal)) from None
            raise

    return idempotency.respond(conn, request, principal.subject, key, body, 201, decide)


@router.post("/candidate-joins/{join_id}/undo", status_code=201, tags=["candidates"])
def undo_join(join_id: str, body: UndoJoinIn, principal: Signed, conn: Db) -> JoinOut:
    """Undoes a join, with a reason. The join itself is kept, marked undone by you."""
    actor = actor_from_principal(principal)
    return _join(joins.undo(conn, actor, decode("join", join_id), body.reason))


@router.get("/candidates/{candidate_id}/group", tags=["candidates"])
def candidate_group(candidate_id: str, principal: Signed, conn: Db) -> GroupOut:
    """Which record this one is read under, and every record read under it."""
    actor = reader_from_principal(principal)
    found = joins.group_of(conn, actor, decode("candidate", candidate_id))
    return GroupOut(
        primary_candidate_id=encode("candidate", found["primary_id"]),
        members=[encode("candidate", member) for member in found["members"]],
    )
