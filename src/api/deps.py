import logging
from collections.abc import Iterator
from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.engine import Connection

from auth import AuthError, AuthUnavailable, Principal, TokenVerifier

log = logging.getLogger("talent.auth")

_bearer = HTTPBearer(
    auto_error=False,
    description="A sign-in token from the company identity provider, or from /dev/token in dev.",
)
_CHALLENGE = {"WWW-Authenticate": "Bearer"}


def db_connection(request: Request) -> Iterator[Connection]:
    """One transaction per request, as the app role: committed on success, rolled back on error."""
    with request.app.state.transaction() as connection:
        yield connection


def current_principal(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> Principal:
    """The signed-in user. Every route except health, readiness and dev sign-in depends on this."""
    if credentials is None:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED, "Sign in with your company account.", _CHALLENGE
        )

    verifier: TokenVerifier = request.app.state.verifier
    try:
        return verifier.verify(credentials.credentials)
    except AuthUnavailable as exc:
        log.error("identity provider unreachable", extra={"error": str(exc)})
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, "Sign-in is unavailable. Try again shortly."
        ) from None
    except AuthError as exc:
        log.info("sign-in token rejected", extra={"reason": str(exc)})
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            "Your sign-in is invalid or has expired. Sign in again.",
            _CHALLENGE,
        ) from None
