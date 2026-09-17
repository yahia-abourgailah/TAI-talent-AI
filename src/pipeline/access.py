"""Who is acting, and how a refusal from the database is reported.

A recruiter reaches only applications and openings they own; a TA lead or an admin reaches all
(BR-408). The criteria owner reads evaluations and review items, all of them, and acts on
nothing. Team
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
ADMIN = "admin"
CRITERIA_OWNER = "criteria_owner"


NO_SUCH_RECORD = "No such requisition, candidate or application."

# A candidate who asked us to stop keeping their data is locked: their record, their applications
# and everything about them read as not found for everyone but an admin (BR-504, migration 0013).
# `column` is how the query names the candidate id.
NOT_LOCKED = """
    (:sees_locked OR NOT EXISTS (
       SELECT 1 FROM core.candidate_locked lock WHERE lock.candidate_id = {column}
    ))
"""


def not_locked(column: str) -> str:
    """`column` must name its table — "a.candidate_id", not "candidate_id".

    An unqualified name inside the subquery resolves to the subquery's own column, so
    `lock.candidate_id = candidate_id` compares the row with itself: it is true as soon as any
    candidate anywhere is locked, and every row disappears for everyone. That is not a subtle
    difference in behaviour, so it is refused here rather than reviewed for.
    """
    if "." not in column:
        raise ValueError(
            f"not_locked({column!r}) needs the table: an unqualified column binds to the "
            "subquery and hides every row once anyone is locked."
        )
    return NOT_LOCKED.format(column=column)


class PipelineError(Exception):
    """A request the pipeline will not carry out. The message is safe to show.

    `code` is stable for API callers; `details` holds ids and codes that help them recover.
    """

    status = 409
    default_code = "refused"

    def __init__(
        self, message: str, *, code: str | None = None, details: Mapping[str, Any] | None = None
    ) -> None:
        super().__init__(message)
        self.code = code or self.default_code
        self.details = dict(details) if details else None


class NotFound(PipelineError):
    status = 404
    default_code = "not_found"


class Refused(PipelineError):
    status = 409
    default_code = "refused"


class NotPermitted(PipelineError):
    status = 403
    default_code = "forbidden"


@dataclass(frozen=True, slots=True)
class Actor:
    subject: str
    sees_all: bool
    is_admin: bool = False

    def scope(self) -> dict[str, Any]:
        return {
            "sees_all": self.sees_all,
            "subject": self.subject,
            "sees_locked": self.is_admin,
        }


def actor_from_principal(principal: Principal) -> Actor:
    """Who works in the pipeline: reads, moves and decides. Every action records the subject."""
    if principal.roles & {TA_LEAD, ADMIN}:
        return Actor(principal.subject, sees_all=True, is_admin=ADMIN in principal.roles)
    if RECRUITER in principal.roles:
        return Actor(principal.subject, sees_all=False)
    raise NotPermitted("Your role does not work in the pipeline.")


def reader_from_principal(principal: Principal) -> Actor:
    """Who reads evaluations and review items. The criteria owner reads all of them; routes that
    act still ask actor_from_principal, which refuses the criteria owner."""
    if principal.roles & {TA_LEAD, ADMIN, RECRUITER}:
        return actor_from_principal(principal)
    if CRITERIA_OWNER in principal.roles:
        return Actor(principal.subject, sees_all=True)
    raise NotPermitted("Your role does not read evaluations or review items.")


# Our own trigger and check messages: ids, step codes and reason codes only.
_SAFE_STATES = {"23514", "42501"}

# Each of migration 0005's refusal messages, by a phrase only it contains, to a stable API code.
# Checked in order; the last one is the most general.
_CODES = (
    ("nothing moves out of it", "stage_is_final"),
    ("no move may leave it", "stage_is_final"),
    ("nothing to review", "stage_is_final"),
    ("is not an allowed move", "transition_not_allowed"),
    ("the first move is to", "first_stage_required"),
    ("is not on the active step list", "unknown_stage"),
    ("moves follow the active step list", "step_list_changed"),
    ("needs a reason from the list", "rejection_reason_required"),
    ("only a rejection carries a reason", "reason_not_allowed"),
    ("only a person rejects", "person_required"),
    ("migrated candidates get no applications", "candidate_not_eligible"),
    ("closed, so it takes no applications", "requisition_closed"),
    ("a closed opening is final", "requisition_closed"),
    ("as its latest move, is reversed", "not_a_rejection"),
    ("still has unchecked fields", "candidate_not_checked"),
    ("a proposed rejection is confirmed or dismissed", "decision_not_allowed"),
    ("only a proposed rejection is confirmed", "decision_not_allowed"),
    (" is at ", "stage_changed"),
)


def _code_for(message: str) -> str:
    return next((code for phrase, code in _CODES if phrase in message), "refused")


def _translate(exc: DBAPIError) -> Exception:
    state = getattr(exc.orig, "sqlstate", None)
    if state == "23503":
        return NotFound(NO_SUCH_RECORD)
    if state == "23505":
        return Refused("That already exists.", code="already_exists")
    if state in _SAFE_STATES:
        diag = getattr(exc.orig, "diag", None)
        message = getattr(diag, "message_primary", None)
        if not message:
            return Refused("The database refused the change.")
        return Refused(str(message), code=_code_for(str(message)))
    return exc


def run(conn: Connection, statement: TextClause, params: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Runs one statement in a savepoint, so a refusal leaves the connection usable."""
    try:
        with conn.begin_nested():
            result = conn.execute(statement, dict(params))
            return [dict(row) for row in result.mappings()] if result.returns_rows else []
    except DBAPIError as exc:
        raise _translate(exc) from None
