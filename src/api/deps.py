import logging
from typing import Annotated

from fastapi import Header, HTTPException, Request, status

from auth import DEV_PRINCIPAL, AuthError, AuthUnavailable, Principal, TokenVerifier

log = logging.getLogger("talent.auth")

_CHALLENGE = {"WWW-Authenticate": "Bearer"}


def current_principal(
    request: Request, authorization: Annotated[str | None, Header()] = None
) -> Principal:
    """The signed-in user. Every route except health and readiness depends on this."""
    if request.app.state.settings.auth_dev_bypass:
        return DEV_PRINCIPAL

    scheme, _, token = (authorization or "").partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED, "Sign in with your company account.", _CHALLENGE
        )

    verifier: TokenVerifier = request.app.state.verifier
    try:
        return verifier.verify(token)
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
