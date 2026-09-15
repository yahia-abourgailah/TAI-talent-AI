"""The event feed (API plan section 7): the events the webhooks deliver, for catching up.

    GET /v1/events?after=evt_120&type=application.stage_changed&limit=200

Oldest first. Resume after the last event you processed with `after`, or page with `cursor`.
Events are kept 30 days in the feed, and carry ids and codes only.
"""

from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.engine import Connection

from api.deps import current_principal, db_connection
from api.errors import ApiError
from api.ids import decode_filter
from api.pages import DEFAULT_LIMIT, MAX_LIMIT, decode_cursor, next_cursor
from auth import Principal
from integrations import events
from pipeline.access import actor_from_principal

router = APIRouter(prefix="/v1", tags=["events"])

EventType = Literal["application.stage_changed", "review.item_created", "candidate.scored"]


class EventOut(BaseModel):
    id: str
    type: str
    api_version: str
    occurred_at: str
    data: dict[str, Any]


class EventPage(BaseModel):
    items: list[EventOut]
    next_cursor: str | None


@router.get("/events")
def list_events(
    principal: Annotated[Principal, Depends(current_principal)],
    conn: Annotated[Connection, Depends(db_connection)],
    limit: Annotated[int, Query(ge=1, le=MAX_LIMIT)] = DEFAULT_LIMIT,
    cursor: Annotated[str | None, Query(max_length=200)] = None,
    after: Annotated[
        str | None, Query(max_length=40, description="the last event id you processed")
    ] = None,
    types: Annotated[list[EventType] | None, Query(alias="type")] = None,
) -> EventPage:
    """Events in your scope, oldest first. De-duplicate on `id`: webhooks may repeat one."""
    actor = actor_from_principal(principal)
    if cursor is not None and after is not None:
        raise ApiError(400, "invalid_request", "Send cursor or after, not both.")
    position = (
        decode_cursor(cursor, "after")
        if cursor is not None
        else decode_filter("event", "after", after)
    )
    rows = events.list_events(conn, actor, limit=limit + 1, after=position, types=types)
    return EventPage(
        items=[EventOut(**events.envelope(row)) for row in rows[:limit]],
        next_cursor=next_cursor(rows, limit, "after"),
    )
