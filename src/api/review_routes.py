"""The review queue (API plan section 6, BR-405, BR-407): what waits for a person.

Week 4 has one kind, negative_verdict: an automated "reject" waiting for a person. They confirm it,
which records the rejection as their own move, or dismiss it with a written reason, which is kept
as a labelled signal for criteria reviews (BR-406). More kinds join in later weeks (flagged
documents, unverified candidates, possible duplicates, borderline scores); a caller must handle a
kind it does not know.

The criteria owner reads the whole queue and cannot resolve anything.
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
from pipeline import store
from pipeline.access import Refused, actor_from_principal, reader_from_principal

router = APIRouter(prefix="/v1", tags=["review queue"])

Signed = Annotated[Principal, Depends(current_principal)]
Db = Annotated[Connection, Depends(db_connection)]

KIND_OUT = {"proposed_rejection": "negative_verdict"}
KIND_IN = {api: db for db, api in KIND_OUT.items()}


class ResolutionOut(BaseModel):
    decision: str
    reason: str | None
    transition_id: str | None
    resolved_by: str
    resolved_at: Timestamp


class ReviewItemOut(BaseModel):
    id: str
    kind: str
    application_id: str
    candidate_id: str
    requisition_id: str
    at_stage: str
    reason_code: str
    proposed_by: str
    proposed_at: Timestamp
    resolution: ResolutionOut | None


class ReviewItemPage(BaseModel):
    items: list[ReviewItemOut]
    next_cursor: str | None


class ResolutionIn(BaseModel):
    decision: Literal["confirm", "dismiss"]
    reason: Annotated[str, Field(min_length=1, max_length=500, pattern=r"\S")] | None = None


def _item(row: Mapping[str, Any]) -> ReviewItemOut:
    resolution = None
    if row["resolution"] is not None:
        move = row["resolution_move_id"]
        resolution = ResolutionOut(
            decision=row["resolution"],
            reason=row["resolution_reason"],
            transition_id=None if move is None else encode("transition", move),
            resolved_by=row["resolved_by"],
            resolved_at=row["resolved_at"],
        )
    return ReviewItemOut(
        id=encode("review_item", row["id"]),
        kind=KIND_OUT.get(row["kind"], row["kind"]),
        application_id=encode("application", row["application_id"]),
        candidate_id=encode("candidate", row["candidate_id"]),
        requisition_id=encode("requisition", row["opening_id"]),
        at_stage=row["at_step"],
        reason_code=row["reason_code"],
        proposed_by=row["proposed_by"],
        proposed_at=row["proposed_at"],
        resolution=resolution,
    )


@router.get("/review-items")
def list_review_items(
    principal: Signed,
    conn: Db,
    limit: Annotated[int, Query(ge=1, le=MAX_LIMIT)] = DEFAULT_LIMIT,
    cursor: Annotated[str | None, Query(max_length=200)] = None,
    status: Annotated[Literal["open", "resolved"], Query()] = "open",
    kind: Annotated[Literal["negative_verdict"] | None, Query()] = None,
    requisition_id: Annotated[str | None, Query(max_length=40)] = None,
) -> ReviewItemPage:
    """Items in your scope, newest first. Open items by default."""
    rows = store.list_review_items(
        conn,
        reader_from_principal(principal),
        limit=limit + 1,
        before=decode_cursor(cursor),
        status=status,
        kind=None if kind is None else KIND_IN[kind],
        opening_id=decode_filter("requisition", "requisition_id", requisition_id),
    )
    return ReviewItemPage(
        items=[_item(row) for row in rows[:limit]], next_cursor=next_cursor(rows, limit)
    )


@router.get("/review-items/{review_item_id}")
def get_review_item(review_item_id: str, principal: Signed, conn: Db) -> ReviewItemOut:
    actor = reader_from_principal(principal)
    return _item(store.get_review_item(conn, actor, decode("review_item", review_item_id)))


@router.post(
    "/review-items/{review_item_id}/resolution", status_code=201, response_model=ReviewItemOut
)
def resolve_review_item(
    review_item_id: str,
    body: ResolutionIn,
    request: Request,
    principal: Signed,
    conn: Db,
    key: idempotency.KeyHeader = None,
) -> JSONResponse:
    """Confirm: the rejection becomes your move, with the proposed reason. Dismiss: needs a
    reason, and the application stays where it is. Either way it is recorded with your name."""
    actor = actor_from_principal(principal)
    number = decode("review_item", review_item_id)

    def resolve() -> ReviewItemOut:
        item = store.get_review_item(conn, actor, number)
        if item["resolution"] is not None:
            raise Refused("This review item is already resolved.", code="review_item_resolved")
        if body.decision == "confirm":
            store.confirm_proposed_rejection(conn, actor, number)
        elif body.reason is None:
            raise ApiError(400, "invalid_request", "Dismissing a review item needs a reason.")
        else:
            store.dismiss_proposed_rejection(conn, actor, number, body.reason)
        return _item(store.get_review_item(conn, actor, number))

    return idempotency.respond(conn, request, principal.subject, key, body, 201, resolve)
