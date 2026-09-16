"""A candidate who asks us to stop keeping their data (API plan section 6; BR-504, BR-205).

A TA member records the request the candidate made — by phone, WhatsApp, email or in person — and
the record locks: from that moment it is not read, not listed, not searched, not scored and not
matched with anyone, and nothing about it is sent anywhere. Every route in the API reports a locked
candidate exactly as it reports one that does not exist.

Nothing is deleted. An admin can see the locked records, and lift one that was recorded by mistake,
with a reason.
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
from api.ids import decode, encode
from api.pages import DEFAULT_LIMIT, MAX_LIMIT, decode_cursor, next_cursor
from auth import Principal
from candidates import withdrawal
from pipeline.access import Refused, actor_from_principal

router = APIRouter(prefix="/v1", tags=["candidates"])

Signed = Annotated[Principal, Depends(current_principal)]
Db = Annotated[Connection, Depends(db_connection)]

HowAsked = Literal["phone", "whatsapp", "email", "in_person", "letter", "other"]


class WithdrawalIn(BaseModel):
    asked_how: HowAsked = Field(description="How the candidate asked us")
    asked_at: Timestamp | None = Field(
        default=None, description="When they asked, if it was not just now"
    )
    note: str | None = Field(
        default=None,
        max_length=1000,
        description="How they put it, in your words. No more than they told you.",
    )


class LiftIn(BaseModel):
    reason: str = Field(min_length=1, max_length=1000)


class WithdrawalOut(BaseModel):
    id: str
    candidate_id: str
    asked_how: str
    asked_at: Timestamp
    note: str | None
    recorded_by: str
    recorded_at: Timestamp
    lifted_at: Timestamp | None
    lifted_by: str | None
    lifted_reason: str | None


class WithdrawalPage(BaseModel):
    items: list[WithdrawalOut]
    next_cursor: str | None


def _out(row: Mapping[str, Any]) -> WithdrawalOut:
    return WithdrawalOut(
        id=encode("withdrawal", row["id"]),
        candidate_id=encode("candidate", row["candidate_id"]),
        asked_how=row["asked_how"],
        asked_at=row["asked_at"],
        note=row["note"],
        recorded_by=row["recorded_by"],
        recorded_at=row["recorded_at"],
        lifted_at=row["lifted_at"],
        lifted_by=row["lifted_by"],
        lifted_reason=row["lifted_reason"],
    )


@router.post(
    "/candidates/{candidate_id}/withdrawals", status_code=201, response_model=WithdrawalOut
)
def record_withdrawal(
    candidate_id: str,
    body: WithdrawalIn,
    request: Request,
    principal: Signed,
    conn: Db,
    key: idempotency.KeyHeader = None,
) -> JSONResponse:
    """Records that this candidate asked us to stop keeping their data, and locks the record.

    From here the candidate is not read, listed, searched, scored or matched, and no one but an
    admin sees the record is there. Nothing is deleted, so a request recorded by mistake can be
    lifted by an admin.
    """
    actor = actor_from_principal(principal)
    number = decode("candidate", candidate_id)

    def decide() -> WithdrawalOut:
        try:
            return _out(
                withdrawal.record(
                    conn,
                    actor,
                    number,
                    asked_how=body.asked_how,
                    asked_at=body.asked_at,
                    note=body.note,
                )
            )
        except Refused as refusal:
            if refusal.code == "invalid_request":
                raise ApiError(400, refusal.code, str(refusal)) from None
            raise

    return idempotency.respond(conn, request, principal.subject, key, body, 201, decide)


@router.get("/withdrawals", response_model=WithdrawalPage)
def list_withdrawals(
    principal: Signed,
    conn: Db,
    limit: Annotated[int, Query(ge=1, le=MAX_LIMIT)] = DEFAULT_LIMIT,
    cursor: Annotated[str | None, Query()] = None,
    standing: Annotated[bool, Query(description="Only the locks in force")] = True,
) -> WithdrawalPage:
    """The locked records, newest first. Admins only."""
    actor = actor_from_principal(principal)
    rows = withdrawal.listed(
        conn, actor, limit=limit + 1, before=decode_cursor(cursor), standing=standing
    )
    return WithdrawalPage(
        items=[_out(row) for row in rows[:limit]], next_cursor=next_cursor(rows, limit)
    )


@router.get("/candidates/{candidate_id}/withdrawals", response_model=list[WithdrawalOut])
def candidate_withdrawals(candidate_id: str, principal: Signed, conn: Db) -> list[WithdrawalOut]:
    """Every request this candidate made about their data, lifted ones included, newest first."""
    actor = actor_from_principal(principal)
    rows = withdrawal.of_candidate(conn, actor, decode("candidate", candidate_id))
    return [_out(row) for row in rows]


@router.post("/withdrawals/{withdrawal_id}/lift", status_code=201, response_model=WithdrawalOut)
def lift_withdrawal(withdrawal_id: str, body: LiftIn, principal: Signed, conn: Db) -> WithdrawalOut:
    """Lifts a withdrawal recorded by mistake, with a reason. Admins only; the record is kept."""
    actor = actor_from_principal(principal)
    try:
        return _out(withdrawal.lift(conn, actor, decode("withdrawal", withdrawal_id), body.reason))
    except Refused as refusal:
        if refusal.code == "invalid_request":
            raise ApiError(400, refusal.code, str(refusal)) from None
        raise
