from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Self


@dataclass(frozen=True, slots=True)
class Principal:
    """Who is making a request. Attributed actions record ``subject`` (NFR-09)."""

    subject: str
    email: str | None
    name: str | None
    roles: frozenset[str]

    @classmethod
    def from_claims(cls, claims: Mapping[str, Any]) -> Self:
        raw_roles = claims.get("roles") or []
        if isinstance(raw_roles, str):
            raw_roles = [raw_roles]
        email = claims.get("email") or claims.get("preferred_username")
        name = claims.get("name")
        return cls(
            subject=str(claims["sub"]),
            email=str(email) if email else None,
            name=str(name) if name else None,
            roles=frozenset(str(role) for role in raw_roles),
        )


DEV_PRINCIPAL = Principal(
    subject="dev-bypass",
    email=None,
    name="Local developer (login bypass)",
    roles=frozenset({"admin"}),
)
