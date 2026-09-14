import os
import time
from collections.abc import Callable
from typing import Any

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from auth import TokenVerifier
from config import Settings

ISSUER = "https://login.example.com/tenant/v2.0"
AUDIENCE = "talent-platform"

_DEFAULT_KEY = object()


@pytest.fixture(autouse=True)
def _isolate_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """A developer's own TALENT_* variables must not change what a test sees."""
    for name in list(os.environ):
        if name.startswith("TALENT_"):
            monkeypatch.delenv(name)


@pytest.fixture
def make_settings() -> Callable[..., Settings]:
    def make(**overrides: Any) -> Settings:
        values: dict[str, Any] = {
            "env": "dev",
            "db_dsn": "postgresql+psycopg://talent_app:hunter2@localhost:5432/talent",
            "redis_url": "redis://localhost:6379/0",
            "blob_endpoint": "http://localhost:9000",
            "blob_access_key": "access",
            "blob_secret_key": "secret-key-value",
            "oidc_issuer": ISSUER,
            "oidc_audience": AUDIENCE,
        }
        values.update(overrides)
        return Settings(_env_file=None, **values)

    return make


@pytest.fixture(scope="session")
def signing_key() -> rsa.RSAPrivateKey:
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


@pytest.fixture
def issue_token(signing_key: rsa.RSAPrivateKey) -> Callable[..., str]:
    """Signs a token like the identity provider would. Pass a claim as None to omit it."""

    def issue(key: Any = _DEFAULT_KEY, algorithm: str = "RS256", **claims: Any) -> str:
        now = int(time.time())
        payload: dict[str, Any] = {
            "iss": ISSUER,
            "aud": AUDIENCE,
            "sub": "user-123",
            "email": "recruiter@example.com",
            "name": "Test Recruiter",
            "roles": ["recruiter"],
            "iat": now,
            "exp": now + 300,
        }
        payload.update(claims)
        payload = {k: v for k, v in payload.items() if v is not None}
        return jwt.encode(payload, signing_key if key is _DEFAULT_KEY else key, algorithm=algorithm)

    return issue


@pytest.fixture
def verifier(signing_key: rsa.RSAPrivateKey) -> TokenVerifier:
    return TokenVerifier(ISSUER, AUDIENCE, key_resolver=lambda _token: signing_key.public_key())
