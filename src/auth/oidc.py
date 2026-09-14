"""Verifies sign-in tokens issued by the company identity provider (NFR-09).

The platform never handles passwords. The identity provider signs a token; this checks the
signature against the provider's published keys, then the issuer, audience and expiry.
"""

import json
import urllib.request
from collections.abc import Callable
from typing import Any

import jwt
from jwt.exceptions import PyJWKClientConnectionError

from auth.principal import Principal

ALGORITHMS = ["RS256", "ES256"]
REQUIRED_CLAIMS = ["iss", "aud", "sub", "iat", "exp"]

KeyResolver = Callable[[str], Any]


class AuthError(Exception):
    """The token is malformed, expired, badly signed, or issued for something else."""


class AuthUnavailable(Exception):
    """The identity provider's signing keys could not be fetched. Not the user's fault."""


def discover_jwks_uri(issuer: str, timeout: float = 5.0) -> str:
    """Reads the signing-key location from the provider's OpenID discovery document."""
    url = issuer.rstrip("/") + "/.well-known/openid-configuration"
    with urllib.request.urlopen(url, timeout=timeout) as response:
        document: dict[str, Any] = json.load(response)
    return str(document["jwks_uri"])


class TokenVerifier:
    def __init__(self, issuer: str, audience: str, key_resolver: KeyResolver | None = None) -> None:
        self._issuer = issuer
        self._audience = audience
        self._key_resolver = key_resolver

    def verify(self, token: str) -> Principal:
        key = self._signing_key(token)
        try:
            claims: dict[str, Any] = jwt.decode(
                token,
                key,
                algorithms=ALGORITHMS,
                audience=self._audience,
                issuer=self._issuer,
                options={"require": REQUIRED_CLAIMS},
            )
        except jwt.PyJWTError as exc:
            raise AuthError(type(exc).__name__) from exc
        return Principal.from_claims(claims)

    def _signing_key(self, token: str) -> Any:
        try:
            if self._key_resolver is None:
                client = jwt.PyJWKClient(discover_jwks_uri(self._issuer), cache_keys=True)
                self._key_resolver = lambda t: client.get_signing_key_from_jwt(t).key
            return self._key_resolver(token)
        except (PyJWKClientConnectionError, OSError, KeyError, ValueError) as exc:
            raise AuthUnavailable(type(exc).__name__) from exc
        except jwt.PyJWTError as exc:
            raise AuthError(type(exc).__name__) from exc
