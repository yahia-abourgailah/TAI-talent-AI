"""Openings, applications and moves, always scoped to the actor (BR-401 to BR-408).

Every read and write here filters by the actor in the query itself. The rules for moves live in
the database (migration 0005): this module never decides whether a move is allowed, it asks the
database and reports the refusal.
"""

from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Connection

from pipeline.access import Actor, NotFound, NotPermitted, Refused, run

_OPENING = (
    "id, brand, department, track, headcount, status, owner_recruiter, team, "
    "criteria_version_id, created_at, created_by, closed_at, closed_reason, closed_by"
)
_APPLICATION = (
    "id, opening_id, candidate_id, owner_recruiter, team, reopens_application_id, created_at, "
    "created_by, current_step, moves, step_since, step_by, outcome"
)
_MOVE = (
    "id, sequence, list_version, from_step, to_step, reason_code, actor_kind, moved_by, moved_at"
)

# The only actor kind the app writes. A system component proposes; it never moves (BR-405).
PERSON = "person"


# --- Step list ----------------------------------------------------------------------------------


def active_step_list(conn: Connection) -> dict[str, Any]:
    (header,) = run(
        conn,
        text(
            "SELECT version, provisional, source, loaded_at, loaded_by FROM pipeline.step_list "
            "WHERE version = pipeline.active_list()"
        ),
        {},
    )
    version = {"version": header["version"]}
    header["steps"] = run(
        conn,
        text(
            "SELECT code, label, position, outcome FROM pipeline.step "
            "WHERE list_version = :version ORDER BY position"
        ),
        version,
    )
    header["moves"] = run(
        conn,
        text(
            "SELECT m.from_step, m.to_step FROM pipeline.allowed_move m "
            "JOIN pipeline.step f ON f.list_version = m.list_version AND f.code = m.from_step "
            "JOIN pipeline.step t ON t.list_version = m.list_version AND t.code = m.to_step "
            "WHERE m.list_version = :version ORDER BY f.position, t.position"
        ),
        version,
    )
    header["rejection_reasons"] = run(
        conn,
        text(
            "SELECT code, label FROM pipeline.rejection_reason "
            "WHERE list_version = :version ORDER BY code"
        ),
        version,
    )
    return header


def _rejected_step(conn: Connection) -> str:
    (row,) = run(
        conn,
        text(
            "SELECT code FROM pipeline.step "
            "WHERE list_version = pipeline.active_list() AND outcome = 'rejected'"
        ),
        {},
    )
    return str(row["code"])


# --- Openings -----------------------------------------------------------------------------------


def create_opening(
    conn: Connection,
    actor: Actor,
    *,
    brand: str,
    department: str,
    track: str,
    headcount: int,
    team: str,
    owner_recruiter: str | None = None,
    criteria_version_id: str | None = None,
) -> dict[str, Any]:
    """Uses the criteria version given, or the one in force when none is given."""
    owner = owner_recruiter or actor.subject
    if not actor.sees_all and owner != actor.subject:
        raise NotPermitted("A recruiter creates openings they own.")
    rows = run(
        conn,
        text(
            f"""
            INSERT INTO pipeline.opening
              (brand, department, track, headcount, owner_recruiter, team, criteria_version_id,
               created_by)
            SELECT :brand, :department, :track, :headcount, :owner, :team, cv.id, :by
            FROM (
              SELECT id FROM core.criteria_version
              WHERE CASE WHEN CAST(:criteria AS text) IS NULL THEN effective_from <= current_date
                         ELSE id = :criteria END
              ORDER BY effective_from DESC, created_at DESC LIMIT 1
            ) cv
            RETURNING {_OPENING}
            """
        ),
        {
            "brand": brand,
            "department": department,
            "track": track,
            "headcount": headcount,
            "owner": owner,
            "team": team,
            "by": actor.subject,
            "criteria": criteria_version_id,
        },
    )
    if not rows:
        if criteria_version_id is not None:
            raise Refused(f"No criteria version {criteria_version_id}.")
        raise Refused("No criteria version is in force.")
    return rows[0]


def get_opening(conn: Connection, actor: Actor, opening_id: int) -> dict[str, Any]:
    rows = run(
        conn,
        text(
            f"SELECT {_OPENING} FROM pipeline.opening "
            "WHERE id = :id AND (:sees_all OR owner_recruiter = :subject)"
        ),
        {"id": opening_id, **actor.scope()},
    )
    if not rows:
        raise NotFound("Opening not found.")
    return rows[0]


def list_openings(conn: Connection, actor: Actor, limit: int, offset: int) -> list[dict[str, Any]]:
    return run(
        conn,
        text(
            f"SELECT {_OPENING} FROM pipeline.opening "
            "WHERE (:sees_all OR owner_recruiter = :subject) "
            "ORDER BY id DESC LIMIT :limit OFFSET :offset"
        ),
        {"limit": limit, "offset": offset, **actor.scope()},
    )


def close_opening(conn: Connection, actor: Actor, opening_id: int, reason: str) -> dict[str, Any]:
    """Closing is how an opening is archived: with a reason and the person, once (BR-401)."""
    get_opening(conn, actor, opening_id)
    (row,) = run(
        conn,
        text(
            f"""
            UPDATE pipeline.opening
            SET status = 'closed', closed_at = clock_timestamp(), closed_reason = :reason,
                closed_by = :by
            WHERE id = :id
            RETURNING {_OPENING}
            """
        ),
        {"id": opening_id, "reason": reason, "by": actor.subject},
    )
    return row


# --- Applications -------------------------------------------------------------------------------


def create_application(
    conn: Connection,
    actor: Actor,
    opening_id: int,
    candidate_id: int,
    owner_recruiter: str | None = None,
) -> dict[str, Any]:
    opening = get_opening(conn, actor, opening_id)
    owner = owner_recruiter or (opening["owner_recruiter"] if actor.sees_all else actor.subject)
    if not actor.sees_all and owner != actor.subject:
        raise NotPermitted("A recruiter creates applications they own.")
    (row,) = run(
        conn,
        text(
            "INSERT INTO pipeline.application "
            "(opening_id, candidate_id, owner_recruiter, team, created_by) "
            "VALUES (:opening, :candidate, :owner, :team, :by) RETURNING id"
        ),
        {
            "opening": opening_id,
            "candidate": candidate_id,
            "owner": owner,
            "team": opening["team"],
            "by": actor.subject,
        },
    )
    return _application(conn, int(row["id"]))


def _application(conn: Connection, application_id: int) -> dict[str, Any]:
    (row,) = run(
        conn,
        text(f"SELECT {_APPLICATION} FROM pipeline.application_state WHERE id = :id"),
        {"id": application_id},
    )
    return row


def get_application(conn: Connection, actor: Actor, application_id: int) -> dict[str, Any]:
    rows = run(
        conn,
        text(
            f"SELECT {_APPLICATION} FROM pipeline.application_state "
            "WHERE id = :id AND (:sees_all OR owner_recruiter = :subject)"
        ),
        {"id": application_id, **actor.scope()},
    )
    if not rows:
        raise NotFound("Application not found.")
    return rows[0]


def list_applications(
    conn: Connection, actor: Actor, limit: int, offset: int, opening_id: int | None = None
) -> list[dict[str, Any]]:
    return run(
        conn,
        text(
            f"""
            SELECT {_APPLICATION} FROM pipeline.application_state
            WHERE (:sees_all OR owner_recruiter = :subject)
              AND (CAST(:opening AS bigint) IS NULL OR opening_id = :opening)
            ORDER BY id DESC LIMIT :limit OFFSET :offset
            """
        ),
        {"limit": limit, "offset": offset, "opening": opening_id, **actor.scope()},
    )


# --- Moves --------------------------------------------------------------------------------------


def move_application(
    conn: Connection,
    actor: Actor,
    application_id: int,
    *,
    from_step: str,
    to_step: str,
    reason_code: str | None = None,
) -> dict[str, Any]:
    """Records a move by a person. The database refuses anything the active list does not allow."""
    get_application(conn, actor, application_id)
    (row,) = run(
        conn,
        text(
            f"""
            INSERT INTO pipeline.move
              (application_id, from_step, to_step, reason_code, actor_kind, moved_by)
            VALUES (:application, :from_step, :to_step, :reason, '{PERSON}', :by)
            RETURNING {_MOVE}
            """
        ),
        {
            "application": application_id,
            "from_step": from_step,
            "to_step": to_step,
            "reason": reason_code,
            "by": actor.subject,
        },
    )
    return row


def move_history(conn: Connection, actor: Actor, application_id: int) -> list[dict[str, Any]]:
    get_application(conn, actor, application_id)
    return run(
        conn,
        text(f"SELECT {_MOVE} FROM pipeline.move WHERE application_id = :id ORDER BY sequence"),
        {"id": application_id},
    )


# --- Rejections that need a person, and reversals (BR-405, BR-406) ------------------------------


def propose_rejection(
    conn: Connection, application_id: int, reason_code: str, proposed_by: str
) -> int:
    """What an automated "reject" does: a review item for a person. It never moves anything."""
    (row,) = run(
        conn,
        text(
            "INSERT INTO pipeline.review_item (application_id, reason_code, proposed_by) "
            "VALUES (:application, :reason, :by) RETURNING id"
        ),
        {"application": application_id, "reason": reason_code, "by": proposed_by},
    )
    return int(row["id"])


def _review_item(conn: Connection, actor: Actor, review_item_id: int) -> dict[str, Any]:
    rows = run(
        conn,
        text(
            """
            SELECT r.id, r.application_id, r.reason_code
            FROM pipeline.review_item r JOIN pipeline.application a ON a.id = r.application_id
            WHERE r.id = :id AND (:sees_all OR a.owner_recruiter = :subject)
            """
        ),
        {"id": review_item_id, **actor.scope()},
    )
    if not rows:
        raise NotFound("Review item not found.")
    return rows[0]


def confirm_proposed_rejection(
    conn: Connection, actor: Actor, review_item_id: int
) -> dict[str, Any]:
    """A person confirms: the rejection is their move, with the proposed reason."""
    item = _review_item(conn, actor, review_item_id)
    application = get_application(conn, actor, item["application_id"])
    move = move_application(
        conn,
        actor,
        item["application_id"],
        from_step=application["current_step"],
        to_step=_rejected_step(conn),
        reason_code=item["reason_code"],
    )
    run(
        conn,
        text(
            "INSERT INTO pipeline.review_resolution "
            "(review_item_id, outcome, move_id, resolved_by) "
            "VALUES (:item, 'confirmed', :move, :by)"
        ),
        {"item": review_item_id, "move": move["id"], "by": actor.subject},
    )
    return move


def dismiss_proposed_rejection(
    conn: Connection, actor: Actor, review_item_id: int, reason: str
) -> None:
    """A person dismisses: kept, with the reason, as a labelled override signal."""
    _review_item(conn, actor, review_item_id)
    run(
        conn,
        text(
            "INSERT INTO pipeline.review_resolution (review_item_id, outcome, reason, resolved_by) "
            "VALUES (:item, 'dismissed', :reason, :by)"
        ),
        {"item": review_item_id, "reason": reason, "by": actor.subject},
    )


def reverse_rejection(
    conn: Connection, actor: Actor, application_id: int, reason: str
) -> dict[str, Any]:
    """The rejected application stays rejected; a new application reopens it, linked to it."""
    application = get_application(conn, actor, application_id)
    (latest,) = run(
        conn,
        text(
            "SELECT id FROM pipeline.move WHERE application_id = :id ORDER BY sequence DESC LIMIT 1"
        ),
        {"id": application_id},
    )
    run(
        conn,
        text(
            "INSERT INTO pipeline.reversal "
            "(application_id, rejection_move_id, reason, reversed_by) "
            "VALUES (:application, :move, :reason, :by)"
        ),
        {
            "application": application_id,
            "move": latest["id"],
            "reason": reason,
            "by": actor.subject,
        },
    )
    (row,) = run(
        conn,
        text(
            "INSERT INTO pipeline.application "
            "(opening_id, candidate_id, owner_recruiter, team, reopens_application_id, created_by) "
            "VALUES (:opening, :candidate, :owner, :team, :reopens, :by) RETURNING id"
        ),
        {
            "opening": application["opening_id"],
            "candidate": application["candidate_id"],
            "owner": application["owner_recruiter"],
            "team": application["team"],
            "reopens": application_id,
            "by": actor.subject,
        },
    )
    return _application(conn, int(row["id"]))
