"""Who is acting, and how a refusal from the database is reported.

A recruiter reaches only applications and openings they own; a TA lead reaches all (BR-408). Team
scoping waits on HRIS org chart data, so for now ownership is the scope. Something out of scope is
reported as not found, exactly like something that does not exist.

Refusals carry the database's own message for the pipeline's rules, which names ids, steps and
reason codes only. Anything else becomes a fixed message, so no stored value reaches a response.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from sqlalchemy.engine import Connection
from sqlalchemy.exc import DBAPIError
from sqlalchemy.sql.elements import TextClause

from auth import Principal

RECRUITER = "recruiter"
TA_LEAD = "ta_lead"


class PipelineError(Exception):
    """A request the pipeline will not carry out. The message is safe to show."""


class NotFound(PipelineError):
    pass


class Refused(PipelineError):
    pass


class NotPermitted(PipelineError):
    pass


@dataclass(frozen=True, slots=True)
class Actor:
    subject: str
    sees_all: bool

    def scope(self) -> dict[str, Any]:
        return {"sees_all": self.sees_all, "subject": self.subject}


def actor_from_principal(principal: Principal) -> Actor:
    if TA_LEAD in principal.roles:
        return Actor(principal.subject, sees_all=True)
    if RECRUITER in principal.roles:
        return Actor(principal.subject, sees_all=False)
    raise NotPermitted("Your role does not work in the pipeline.")


# Our own trigger and check messages: ids, step codes and reason codes only.
_SAFE_STATES = {"23514", "42501"}


def _translate(exc: DBAPIError) -> Exception:
    state = getattr(exc.orig, "sqlstate", None)
    if state == "23503":
        return NotFound("No such opening, candidate or application.")
    if state == "23505":
        return Refused("That already exists.")
    if state in _SAFE_STATES:
        diag = getattr(exc.orig, "diag", None)
        message = getattr(diag, "message_primary", None)
        return Refused(str(message) if message else "The database refused the change.")
    return exc


def run(conn: Connection, statement: TextClause, params: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Runs one statement in a savepoint, so a refusal leaves the connection usable."""
    try:
        with conn.begin_nested():
            result = conn.execute(statement, dict(params))
            return [dict(row) for row in result.mappings()] if result.returns_rows else []
    except DBAPIError as exc:
        raise _translate(exc) from None
