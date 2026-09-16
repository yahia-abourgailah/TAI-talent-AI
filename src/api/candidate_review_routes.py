"""Review items about a candidate (API plan section 6; BR-407, BR-308, BR-202).

    flagged_document      a CV a person must look at: hidden content was found, or reading it
                          failed. reason_code says which.
    unverified_candidate  a candidate typed in by hand and not yet checked.

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
from candidates import reviews
from pipeline.access import actor_from_principal, reader_from_principal

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


class CandidateReviewItemOut(BaseModel):
    id: str
    kind: str
    candidate_id: str
    document_id: str | None
    reason_code: str
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
    return CandidateReviewItemOut(
        id=encode("review_item", row["id"]),
        kind=row["kind"],
        candidate_id=encode("candidate", row["candidate_id"]),
        document_id=None if capture is None else encode("document", capture),
        reason_code=row["reason_code"],
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
    kind: Annotated[Literal["flagged_document", "unverified_candidate"] | None, Query()] = None,
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
