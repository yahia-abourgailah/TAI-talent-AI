"""What needs a person right now, in one call (BR-407).

    GET /v1/review-queue?kind=borderline_score&limit=50
    GET /v1/reports/review-queue

The queue lists every open review item in your scope, of every kind, oldest first: the order a
person works through them. Each line carries `reason`, a sentence a recruiter reads. Items are
resolved where they live: /v1/review-items for a proposed rejection (negative_verdict), and
/v1/candidate-review-items for the rest. `resolve_at` names the path.

The report counts open items per kind and gives how long the oldest has waited. It is also part
of the funnel report. A TA lead or an admin reads reports.
"""

from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.engine import Connection

from api.deps import current_principal, db_connection
from api.fields import Timestamp
from api.ids import encode
from api.pages import DEFAULT_LIMIT, MAX_LIMIT, decode_cursor, next_cursor
from auth import Principal
from candidates import queue
from pipeline.access import NotPermitted, actor_from_principal, reader_from_principal

router = APIRouter(prefix="/v1", tags=["review queue"])

Signed = Annotated[Principal, Depends(current_principal)]
Db = Annotated[Connection, Depends(db_connection)]

KIND_OUT = {"proposed_rejection": "negative_verdict"}
QueueKind = Literal[
    "negative_verdict",
    "flagged_document",
    "unverified_candidate",
    "possible_duplicate",
    "borderline_score",
    "ai_assessment",
]


class QueueBorderlineOut(BaseModel):
    evaluation_id: str
    tier_above: str
    tier_below: str


class QueueItemOut(BaseModel):
    id: str
    kind: str
    candidate_id: str
    application_id: str | None
    reason_code: str
    reason: str
    borderline: QueueBorderlineOut | None
    waiting_since: Timestamp
    resolve_at: str


class QueuePage(BaseModel):
    items: list[QueueItemOut]
    next_cursor: str | None


class QueueKindCountOut(BaseModel):
    kind: str
    open: int
    oldest_since: Timestamp | None
    oldest_waiting_hours: float | None


class QueueSummaryOut(BaseModel):
    open: int
    oldest_waiting_hours: float | None
    oldest_kind: str | None
    kinds: list[QueueKindCountOut]


def summary_out(found: dict[str, Any]) -> QueueSummaryOut:
    return QueueSummaryOut(
        open=found["open"],
        oldest_waiting_hours=found["oldest_waiting_hours"],
        oldest_kind=(
            None
            if found["oldest_kind"] is None
            else KIND_OUT.get(found["oldest_kind"], found["oldest_kind"])
        ),
        kinds=[
            QueueKindCountOut(**{**kind, "kind": KIND_OUT.get(kind["kind"], kind["kind"])})
            for kind in found["kinds"]
        ],
    )


def _line(row: dict[str, Any]) -> QueueItemOut:
    item_id = encode("review_item", row["id"])
    application = row["application_id"]
    borderline = None
    if row["kind"] == "borderline_score" and row["evaluation_id"] is not None:
        borderline = QueueBorderlineOut(
            evaluation_id=encode("evaluation", row["evaluation_id"]),
            tier_above=row["tier_above"],
            tier_below=row["tier_below"],
        )
    # A proposed rejection is resolved on its application; every other kind is resolved on the
    # candidate, even an assessment, which names the application it was about but is not one.
    path = "review-items" if row["kind"] == "proposed_rejection" else "candidate-review-items"
    return QueueItemOut(
        id=item_id,
        kind=KIND_OUT.get(row["kind"], row["kind"]),
        candidate_id=encode("candidate", row["candidate_id"]),
        application_id=None if application is None else encode("application", application),
        reason_code=row["reason_code"],
        reason=row["reason"],
        borderline=borderline,
        waiting_since=row["proposed_at"],
        resolve_at=f"/v1/{path}/{item_id}/resolution",
    )


@router.get("/review-queue")
def review_queue(
    principal: Signed,
    conn: Db,
    limit: Annotated[int, Query(ge=1, le=MAX_LIMIT)] = DEFAULT_LIMIT,
    cursor: Annotated[str | None, Query(max_length=200)] = None,
    kind: Annotated[QueueKind | None, Query()] = None,
) -> QueuePage:
    """Every open item in your scope, of every kind, oldest first, each with its reason."""
    stored_kind = {v: k for k, v in KIND_OUT.items()}.get(kind, kind) if kind else None
    rows = queue.list_open(
        conn,
        reader_from_principal(principal),
        limit=limit + 1,
        after=decode_cursor(cursor, "after"),
        kind=stored_kind,
    )
    return QueuePage(
        items=[_line(row) for row in rows[:limit]], next_cursor=next_cursor(rows, limit, "after")
    )


@router.get("/reports/review-queue", tags=["reports"])
def review_queue_report(principal: Signed, conn: Db) -> QueueSummaryOut:
    """Open items per kind, and how long the oldest has waited. Counts only."""
    actor = actor_from_principal(principal)
    if not actor.sees_all:
        raise NotPermitted("Reports are for a TA lead or an admin.")
    return summary_out(queue.summary(conn, actor))
