import time

import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from jwt.exceptions import PyJWKClientConnectionError

from auth import AuthError, AuthUnavailable, TokenVerifier
from auth.principal import Principal

from .conftest import AUDIENCE, ISSUER


def test_valid_token_yields_principal(verifier, issue_token):
    principal = verifier.verify(issue_token())
    assert principal == Principal(
        subject="user-123",
        email="recruiter@example.com",
        name="Test Recruiter",
        roles=frozenset({"recruiter"}),
    )


def test_single_role_string_is_accepted(verifier, issue_token):
    assert verifier.verify(issue_token(roles="admin")).roles == frozenset({"admin"})


def test_email_falls_back_to_preferred_username(verifier, issue_token):
    token = issue_token(email=None, preferred_username="recruiter@example.com")
    assert verifier.verify(token).email == "recruiter@example.com"


@pytest.mark.parametrize(
    "claims",
    [
        pytest.param({"exp": int(time.time()) - 60}, id="expired"),
        pytest.param({"aud": "another-app"}, id="wrong-audience"),
        pytest.param({"iss": "https://attacker.example.com"}, id="wrong-issuer"),
        pytest.param({"sub": None}, id="no-subject"),
        pytest.param({"exp": None}, id="no-expiry"),
    ],
)
def test_rejected_claims(verifier, issue_token, claims):
    with pytest.raises(AuthError):
        verifier.verify(issue_token(**claims))


def test_unsigned_token_rejected(verifier, issue_token):
    with pytest.raises(AuthError):
        verifier.verify(issue_token(key="", algorithm="none"))


def test_token_signed_by_another_key_rejected(verifier, issue_token):
    stranger = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    with pytest.raises(AuthError):
        verifier.verify(issue_token(key=stranger))


def test_garbage_token_rejected(verifier):
    with pytest.raises(AuthError):
        verifier.verify("not-a-token")


def test_unreachable_identity_provider_is_unavailable_not_rejected(issue_token):
    def unreachable(_token):
        raise PyJWKClientConnectionError("connection refused")

    verifier = TokenVerifier(ISSUER, AUDIENCE, key_resolver=unreachable)
    with pytest.raises(AuthUnavailable):
        verifier.verify(issue_token())
