"""A candidate who asks us to stop keeping their data (BR-504, BR-205).

The request reaches us the way candidates talk to us: a call, a WhatsApp message, an email, or a
word in the office. A TA member opens the record and records it here, with how the candidate asked
and when. Nobody's record locks itself, and no link in an email does it for them.

What locking means: while the withdrawal stands, the record is not read, not listed, not searched,
not scored, not matched against anyone else, and its applications are neither shown nor moved. Only
an admin can see that a locked record is there, so that a request recorded by mistake can be
lifted, with a reason.

What locking does not mean: nothing is deleted (BR-205). Everything the record holds stays where it
is — which is what makes a lift possible, and what keeps last quarter's counts honest.
"""

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Connection

from candidates.reads import get_candidate
from pipeline.access import Actor, NotFound, NotPermitted, Refused, run

HOW_ASKED = ("phone", "whatsapp", "email", "in_person", "letter", "other")

_COLUMNS = (
    "id, candidate_id, asked_how, asked_at, note, recorded_by, recorded_at, "
    "lifted_at, lifted_by, lifted_reason"
)


def _checked_how(asked_how: str) -> str:
    if asked_how not in HOW_ASKED:
        raise Refused(
            f"How the candidate asked is one of: {', '.join(HOW_ASKED)}.", code="invalid_request"
        )
    return asked_how


def record(
    conn: Connection,
    actor: Actor,
    candidate_id: int,
    *,
    asked_how: str,
    asked_at: datetime | None = None,
    note: str | None = None,
) -> dict[str, Any]:
    """Records that this candidate asked us to stop keeping their data, and locks the record."""
    get_candidate(conn, actor, candidate_id)  # out of scope, or already locked, reads as not found
    how = _checked_how(asked_how)
    when = asked_at or datetime.now(UTC)
    # A request dated after it was recorded is refused by the database's own check.
    rows = run(
        conn,
        text(
            f"""
            INSERT INTO core.consent_withdrawal
              (candidate_id, asked_how, asked_at, note, recorded_by)
            VALUES (:candidate, :how, :asked_at, :note, :by)
            RETURNING {_COLUMNS}
            """
        ),
        {
            "candidate": candidate_id,
            "how": how,
            "asked_at": when,
            "note": (note or "").strip() or None,
            "by": actor.subject,
        },
    )
    return rows[0]


def lift(conn: Connection, actor: Actor, withdrawal_id: int, reason: str) -> dict[str, Any]:
    """Lifts a withdrawal recorded by mistake, with a reason. Admins only: no one else can see it.

    A candidate who has asked to be left alone is not brought back by a recruiter who wants the
    record; a lift is for the request that was never made, or was made about the wrong person.
    """
    if not actor.is_admin:
        raise NotPermitted("Only an admin lifts a withdrawal.")
    if not reason.strip():
        raise Refused("Lifting a withdrawal needs a reason.", code="invalid_request")
    found = conn.execute(
        text("SELECT lifted_at FROM core.consent_withdrawal WHERE id = :id"), {"id": withdrawal_id}
    ).one_or_none()
    if found is None:
        raise NotFound("Withdrawal not found.")
    rows = run(
        conn,
        text(
            f"""
            UPDATE core.consent_withdrawal
            SET lifted_by = :by, lifted_reason = :reason, lifted_at = clock_timestamp()
            WHERE id = :id AND lifted_at IS NULL
            RETURNING {_COLUMNS}
            """
        ),
        {"id": withdrawal_id, "by": actor.subject, "reason": reason.strip()},
    )
    if not rows:
        raise Refused("This withdrawal is already lifted.", code="withdrawal_already_lifted")
    return rows[0]


def is_locked(conn: Connection, candidate_id: int) -> bool:
    """Whether a withdrawal stands on this record. Anything that acts on a candidate asks first."""
    return (
        conn.execute(
            text("SELECT 1 FROM core.candidate_locked WHERE candidate_id = :id"),
            {"id": candidate_id},
        ).first()
        is not None
    )


def of_candidate(conn: Connection, actor: Actor, candidate_id: int) -> list[dict[str, Any]]:
    """Every withdrawal recorded about one candidate, lifted ones included, newest first."""
    if not actor.is_admin:
        get_candidate(conn, actor, candidate_id)
    return run(
        conn,
        text(
            f"SELECT {_COLUMNS} FROM core.consent_withdrawal WHERE candidate_id = :id "
            "ORDER BY id DESC"
        ),
        {"id": candidate_id},
    )


def listed(
    conn: Connection, actor: Actor, *, limit: int, before: int | None = None, standing: bool = True
) -> list[dict[str, Any]]:
    """The locked records, newest first. Admins only: locked records are invisible to everyone."""
    if not actor.is_admin:
        raise NotPermitted("Only an admin reads the locked records.")
    return run(
        conn,
        text(
            f"""
            SELECT {_COLUMNS} FROM core.consent_withdrawal
            WHERE (NOT :standing OR lifted_at IS NULL)
              AND (CAST(:before AS bigint) IS NULL OR id < :before)
            ORDER BY id DESC LIMIT :limit
            """
        ),
        {"limit": limit, "before": before, "standing": standing},
    )
