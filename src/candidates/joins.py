"""Joining two records into one person, and undoing it (BR-204, BR-206).

A join is a person's decision, never the matcher's. Both records stay exactly as they are, with
their own fields, evaluations, applications and history: the join only says which record the other
is read under. Undoing it puts everything back, because nothing was moved.

A joined record cannot itself be a primary, so a group is one level deep (migration 0012). Joining
resolves the review item of the match it came from, as checked by the person who joined.
"""

from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Connection

from candidates.reads import get_candidate
from pipeline.access import Actor, NotFound, Refused, run

JOINED_BY_HAND = "joined by hand"


def _match_between(conn: Connection, lower: int, higher: int) -> int | None:
    return conn.execute(
        text("SELECT id FROM core.candidate_match WHERE lower_id = :lower AND higher_id = :higher"),
        {"lower": min(lower, higher), "higher": max(lower, higher)},
    ).scalar_one_or_none()


def _resolve_review_item(conn: Connection, match_id: int, actor: Actor) -> int | None:
    """The match's review item, marked checked by the person who decided."""
    item = conn.execute(
        text(
            """
            SELECT r.id FROM pipeline.review_item r
            WHERE r.kind = 'possible_duplicate' AND r.match_id = :match
              AND NOT EXISTS (
                SELECT 1 FROM pipeline.review_resolution x WHERE x.review_item_id = r.id
              )
            """
        ),
        {"match": match_id},
    ).scalar_one_or_none()
    if item is None:
        return None
    run(
        conn,
        text(
            "INSERT INTO pipeline.review_resolution (review_item_id, outcome, resolved_by) "
            "VALUES (:item, 'checked', :by)"
        ),
        {"item": int(item), "by": actor.subject},
    )
    return int(item)


def join(
    conn: Connection,
    actor: Actor,
    primary_id: int,
    joined_id: int,
    reason: str,
    match_id: int | None = None,
) -> dict[str, Any]:
    """Records that two candidates are one person. Both must be in the actor's scope."""
    if primary_id == joined_id:
        raise Refused("A record cannot be joined into itself.", code="invalid_request")
    if not reason.strip():
        raise Refused("A join needs a reason.", code="invalid_request")
    get_candidate(conn, actor, primary_id)
    get_candidate(conn, actor, joined_id)
    match = match_id if match_id is not None else _match_between(conn, primary_id, joined_id)
    (row,) = run(
        conn,
        text(
            """
            INSERT INTO core.candidate_join (primary_id, joined_id, match_id, reason, joined_by)
            VALUES (:primary, :joined, :match, :reason, :by)
            RETURNING id, primary_id, joined_id, match_id, reason, joined_by, joined_at,
                      undone_at, undone_by, undone_reason
            """
        ),
        {
            "primary": primary_id,
            "joined": joined_id,
            "match": match,
            "reason": reason.strip(),
            "by": actor.subject,
        },
    )
    if match is not None:
        _resolve_review_item(conn, int(match), actor)
    return row


def undo(conn: Connection, actor: Actor, join_id: int, reason: str) -> dict[str, Any]:
    """Undoes a join, with a reason and the person's name. The join itself is kept."""
    if not reason.strip():
        raise Refused("Undoing a join needs a reason.", code="invalid_request")
    found = conn.execute(
        text("SELECT primary_id, joined_id, undone_at FROM core.candidate_join WHERE id = :id"),
        {"id": join_id},
    ).one_or_none()
    if found is None:
        raise NotFound("Join not found.")
    get_candidate(conn, actor, int(found.primary_id))
    rows = run(
        conn,
        text(
            """
            UPDATE core.candidate_join
            SET undone_by = :by, undone_reason = :reason, undone_at = clock_timestamp()
            WHERE id = :id AND undone_at IS NULL
            RETURNING id, primary_id, joined_id, match_id, reason, joined_by, joined_at,
                      undone_at, undone_by, undone_reason
            """
        ),
        {"id": join_id, "by": actor.subject, "reason": reason.strip()},
    )
    if not rows:
        raise Refused("This join is already undone.", code="join_already_undone")
    return rows[0]


def group_of(conn: Connection, actor: Actor, candidate_id: int) -> dict[str, Any]:
    """Which record a candidate is read under, and every record read under it."""
    get_candidate(conn, actor, candidate_id)
    primary = conn.execute(
        text("SELECT primary_id FROM core.candidate_group WHERE candidate_id = :id"),
        {"id": candidate_id},
    ).scalar_one()
    members = (
        conn.execute(
            text(
                "SELECT candidate_id FROM core.candidate_group WHERE primary_id = :id "
                "ORDER BY candidate_id"
            ),
            {"id": primary},
        )
        .scalars()
        .all()
    )
    return {"primary_id": int(primary), "members": [int(member) for member in members]}


def joins_of(conn: Connection, candidate_id: int) -> list[dict[str, Any]]:
    """Every join this candidate was part of, undone ones included, newest first."""
    return run(
        conn,
        text(
            """
            SELECT id, primary_id, joined_id, match_id, reason, joined_by, joined_at,
                   undone_at, undone_by, undone_reason
            FROM core.candidate_join
            WHERE primary_id = :id OR joined_id = :id
            ORDER BY id DESC
            """
        ),
        {"id": candidate_id},
    )
