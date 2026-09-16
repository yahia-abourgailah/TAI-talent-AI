"""A tracking code per job post, so we know which post brought a candidate (BR-602).

A recruiter asks for a code when they publish a requisition somewhere: one code per post, never
reused, never guessable in a useful way. The link in the post carries it, the apply page sends it
back, and it is kept with the candidate's consent record and their application.

A code names a job post, nothing more: it is not a secret and gives no access. An unknown code is
refused rather than quietly dropped, so a broken link is noticed.
"""

import re
import secrets
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Connection

from pipeline.access import Actor, NotFound, Refused, run
from pipeline.store import get_opening

CODE = re.compile(r"^[a-z0-9][a-z0-9-]{3,39}$")
CHANNEL = re.compile(r"^[a-z][a-z_]{1,29}$")
_RANDOM_PART = 4


def new_code(channel: str) -> str:
    """A short code that says where the post is, with random characters after it."""
    return f"{channel.replace('_', '-')[:12]}-{secrets.token_hex(_RANDOM_PART)}"


def issue(
    conn: Connection,
    actor: Actor,
    opening_id: int,
    *,
    channel: str,
    label: str | None = None,
    code: str | None = None,
) -> dict[str, Any]:
    """A code for one job post of a requisition the actor can see."""
    get_opening(conn, actor, opening_id)
    if not CHANNEL.fullmatch(channel):
        raise Refused("A channel is lower-case letters, such as tiktok.", code="invalid_channel")
    wanted = code or new_code(channel)
    if not CODE.fullmatch(wanted):
        raise Refused(
            "A tracking code is 4 to 40 lower-case letters, digits or dashes.",
            code="invalid_tracking_code",
        )
    (row,) = run(
        conn,
        text(
            """
            INSERT INTO pipeline.job_post (opening_id, code, channel, label, created_by)
            VALUES (:opening, :code, :channel, :label, :by)
            RETURNING id, opening_id, code, channel, label, created_at, created_by
            """
        ),
        {
            "opening": opening_id,
            "code": wanted,
            "channel": channel,
            "label": label,
            "by": actor.subject,
        },
    )
    return row


def list_for_opening(conn: Connection, actor: Actor, opening_id: int) -> list[dict[str, Any]]:
    get_opening(conn, actor, opening_id)
    return run(
        conn,
        text(
            "SELECT id, opening_id, code, channel, label, created_at, created_by "
            "FROM pipeline.job_post WHERE opening_id = :opening ORDER BY id"
        ),
        {"opening": opening_id},
    )


def resolve(conn: Connection, code: str) -> dict[str, Any]:
    """The job post a code names. Not found when nothing matches."""
    rows = run(
        conn,
        text("SELECT id, opening_id, code, channel FROM pipeline.job_post WHERE code = :code"),
        {"code": code},
    )
    if not rows:
        raise NotFound("Job post not found.")
    return rows[0]
