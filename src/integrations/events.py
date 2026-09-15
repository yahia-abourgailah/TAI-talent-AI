"""The event feed: what the webhooks deliver, readable for 30 days (API plan section 7).

The database writes events (migration 0007); this module only reads them. A recruiter sees the
events of their own applications; a TA lead sees every event, including candidate.scored, which
belongs to no application yet.
"""

from collections.abc import Mapping, Sequence
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Connection

from api.fields import utc
from api.ids import encode
from pipeline.access import Actor, run

API_VERSION = "v1"
RETENTION_DAYS = 30
TYPES = ("application.stage_changed", "review.item_created", "candidate.scored")


def envelope(row: Mapping[str, Any]) -> dict[str, Any]:
    """An event as the CRM receives it, by webhook or from the feed."""
    return {
        "id": encode("event", int(row["id"])),
        "type": row["type"],
        "api_version": API_VERSION,
        "occurred_at": utc(row["occurred_at"]),
        "data": dict(row["data"]),
    }


def list_events(
    conn: Connection,
    actor: Actor,
    *,
    limit: int,
    after: int | None = None,
    types: Sequence[str] | None = None,
) -> list[dict[str, Any]]:
    """Oldest first, ids above `after`, so a caller resumes from the last event it processed."""
    return run(
        conn,
        text(
            """
            SELECT e.id, e.type, e.occurred_at, e.data
            FROM integration.event e
            LEFT JOIN pipeline.application a ON a.id = e.application_id
            WHERE e.recorded_at > clock_timestamp() - make_interval(days => :days)
              AND (:sees_all OR a.owner_recruiter = :subject)
              AND (CAST(:after AS bigint) IS NULL OR e.id > :after)
              AND (CAST(:types AS text[]) IS NULL OR e.type = ANY(CAST(:types AS text[])))
            ORDER BY e.id LIMIT :limit
            """
        ),
        {
            "days": RETENTION_DAYS,
            "after": after,
            "types": list(types) if types else None,
            "limit": limit,
            **actor.scope(),
        },
    )
