"""Development sign-in with fake accounts, and putting away what a test run made.

Mounted only when TALENT_AUTH_MODE=dev, and every route here checks TALENT_ENV as well.
"""

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.engine import Connection

from api.deps import db_connection
from auth import DEV_ACCOUNTS, DevIdentity, UnknownDevAccountError
from auth.dev_identity import TOKEN_LIFETIME_SECONDS
from config import Environment

log = logging.getLogger("talent.auth")

Db = Annotated[Connection, Depends(db_connection)]

CLEARED_BY = "dev-clear-out"
CLEARED_REASON = "Made while trying the platform out (dev)"
# Where the 5,140 came from. Anything else in a dev database was made by somebody trying things.
IMPORT_SOURCE = "migrated from TAI_Master"

_IMPORTED = """
    EXISTS (
      SELECT 1 FROM core.candidate_field f
      WHERE f.candidate_id = c.id AND f.source = :import_source
    )
"""
_IMPORTED_COUNT = f"SELECT count(*) FROM core.candidate c WHERE {_IMPORTED}"
_ARCHIVE_TEST_CANDIDATES = f"""
    UPDATE core.candidate c
    SET archived_at = clock_timestamp(), archived_reason = :reason, archived_by = :by
    WHERE c.archived_at IS NULL AND NOT {_IMPORTED}
"""
_CLOSE_OPEN_REQUISITIONS = """
    UPDATE pipeline.opening
    SET status = 'closed', closed_at = clock_timestamp(), closed_reason = :reason, closed_by = :by
    WHERE status = 'open'
"""

router = APIRouter(prefix="/dev", tags=["development sign-in"])


class DevAccountOut(BaseModel):
    account: str
    name: str
    email: str
    roles: list[str]
    purpose: str


class TokenRequest(BaseModel):
    account: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int = TOKEN_LIFETIME_SECONDS
    account: str


@router.get("/accounts")
def list_accounts() -> list[DevAccountOut]:
    """The fake accounts you can sign in as."""
    return [
        DevAccountOut(
            account=a.key, name=a.name, email=a.email, roles=list(a.roles), purpose=a.purpose
        )
        for a in DEV_ACCOUNTS.values()
    ]


@router.post("/token")
def issue_token(body: TokenRequest, request: Request) -> TokenResponse:
    """Signs in as a fake account. Send the token as ``Authorization: Bearer <token>``."""
    identity: DevIdentity = request.app.state.dev_identity
    try:
        token = identity.issue(body.account)
    except UnknownDevAccountError:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            f"No dev account '{body.account}'. Choose one of: {', '.join(DEV_ACCOUNTS)}.",
        ) from None
    log.info("dev token issued", extra={"account": body.account})
    return TokenResponse(access_token=token, account=body.account)


class ClearedOut(BaseModel):
    """What a clear-out did. Nothing is deleted: this is the same archiving and closing a person
    does by hand, done in one call (BR-205, BR-401)."""

    candidates_archived: int
    requisitions_closed: int
    kept_from_the_import: int


@router.post("/clear-test-data", tags=["development"])
def clear_test_data(request: Request, conn: Db) -> ClearedOut:
    """Puts away everything made while trying the platform out. Dev only.

    Archives every candidate that did not come from the TAI_Master import, and closes every open
    requisition. Nothing is deleted — the app role has no DELETE grant and the tables refuse it —
    so this is exactly what a person would do one row at a time, with a reason and a name on it.

    The 5,140 records imported from the sheet are left alone: they are not test data.
    """
    if request.app.state.settings.env is not Environment.DEV:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not Found")
    kept = conn.execute(text(_IMPORTED_COUNT), {"import_source": IMPORT_SOURCE}).scalar_one()
    candidates = conn.execute(
        text(_ARCHIVE_TEST_CANDIDATES),
        {"reason": CLEARED_REASON, "by": CLEARED_BY, "import_source": IMPORT_SOURCE},
    ).rowcount
    openings = conn.execute(
        text(_CLOSE_OPEN_REQUISITIONS), {"reason": CLEARED_REASON, "by": CLEARED_BY}
    ).rowcount
    log.warning(
        "dev clear-out: %s candidates archived, %s requisitions closed", candidates, openings
    )
    return ClearedOut(
        candidates_archived=candidates,
        requisitions_closed=openings,
        kept_from_the_import=kept,
    )
