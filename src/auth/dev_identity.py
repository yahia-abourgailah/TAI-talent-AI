"""Fake company accounts for development (TALENT_AUTH_MODE=dev).

A token for a fake account is signed with a key generated when the app starts, then checked by
the same TokenVerifier as a real sign-in: signature, issuer, audience and expiry. Every protected
route therefore runs its production verification path. Settings refuse this mode outside dev, and
restarting the app invalidates every dev token.
"""

import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass

import jwt
from cryptography.hazmat.primitives.asymmetric import ec

from auth.oidc import TokenVerifier

# .invalid is reserved (RFC 2606): these can never collide with a real provider or mailbox.
DEV_ISSUER = "https://dev-identity.talent.invalid"
DEV_AUDIENCE = "talent-platform-dev"
TOKEN_LIFETIME_SECONDS = 8 * 60 * 60


@dataclass(frozen=True, slots=True)
class DevAccount:
    key: str
    name: str
    roles: tuple[str, ...]
    purpose: str

    @property
    def email(self) -> str:
        return f"{self.key}@dev.talent.invalid"

    @property
    def subject(self) -> str:
        return f"dev|{self.key}"


DEV_ACCOUNTS: dict[str, DevAccount] = {
    account.key: account
    for account in (
        DevAccount("recruiter-a", "Dev Recruiter A", ("recruiter",), "A recruiter"),
        DevAccount(
            "recruiter-b",
            "Dev Recruiter B",
            ("recruiter",),
            "A second recruiter, to check recruiters only see their own candidates (BR-408)",
        ),
        DevAccount("ta-lead", "Dev TA Lead", ("ta_lead",), "TA leadership, sees the whole funnel"),
        DevAccount(
            "criteria-owner",
            "Dev Criteria Owner",
            ("criteria_owner",),
            "Rules on criteria versions and replay differences",
        ),
        DevAccount("admin", "Dev Admin", ("admin",), "Platform administration"),
    )
}


class UnknownDevAccountError(KeyError):
    """No fake account has that key."""


class DevIdentity:
    def __init__(self, clock: Callable[[], float] = time.time) -> None:
        self._private_key = ec.generate_private_key(ec.SECP256R1())
        self._key_id = uuid.uuid4().hex
        self._clock = clock

    def issue(self, account_key: str) -> str:
        account = DEV_ACCOUNTS.get(account_key)
        if account is None:
            raise UnknownDevAccountError(account_key)
        now = int(self._clock())
        claims = {
            "iss": DEV_ISSUER,
            "aud": DEV_AUDIENCE,
            "sub": account.subject,
            "email": account.email,
            "name": account.name,
            "roles": list(account.roles),
            "iat": now,
            "exp": now + TOKEN_LIFETIME_SECONDS,
        }
        return jwt.encode(
            claims, self._private_key, algorithm="ES256", headers={"kid": self._key_id}
        )

    def verifier(self) -> TokenVerifier:
        public_key = self._private_key.public_key()
        return TokenVerifier(DEV_ISSUER, DEV_AUDIENCE, key_resolver=lambda _token: public_key)
