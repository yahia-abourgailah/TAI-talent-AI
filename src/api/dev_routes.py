"""Development sign-in with fake accounts. Mounted only when TALENT_AUTH_MODE=dev."""

import logging

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel

from auth import DEV_ACCOUNTS, DevIdentity, UnknownDevAccountError
from auth.dev_identity import TOKEN_LIFETIME_SECONDS

log = logging.getLogger("talent.auth")

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
