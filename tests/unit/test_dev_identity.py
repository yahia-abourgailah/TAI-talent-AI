import time

import pytest

from auth import DEV_ACCOUNTS, AuthError, DevIdentity, TokenVerifier, UnknownDevAccountError
from auth.dev_identity import DEV_AUDIENCE, DEV_ISSUER

from .conftest import AUDIENCE, ISSUER


def test_every_fake_account_signs_in_with_its_roles():
    identity = DevIdentity()
    verifier = identity.verifier()
    for key, account in DEV_ACCOUNTS.items():
        principal = verifier.verify(identity.issue(key))
        assert principal.subject == f"dev|{key}"
        assert principal.email.endswith("@dev.talent.invalid")
        assert principal.roles == frozenset(account.roles)


def test_two_recruiters_are_distinct_people():
    identity = DevIdentity()
    verifier = identity.verifier()
    a = verifier.verify(identity.issue("recruiter-a"))
    b = verifier.verify(identity.issue("recruiter-b"))
    assert a.subject != b.subject
    assert a.roles == b.roles == frozenset({"recruiter"})


def test_unknown_account_is_refused():
    with pytest.raises(UnknownDevAccountError):
        DevIdentity().issue("root")


def test_expired_dev_token_is_rejected():
    past = time.time() - 9 * 60 * 60
    identity = DevIdentity(clock=lambda: past)
    with pytest.raises(AuthError):
        identity.verifier().verify(identity.issue("recruiter-a"))


def test_token_from_another_app_start_is_rejected():
    before_restart, after_restart = DevIdentity(), DevIdentity()
    with pytest.raises(AuthError):
        after_restart.verifier().verify(before_restart.issue("admin"))


def test_dev_token_is_never_accepted_as_company_sign_in():
    identity = DevIdentity()
    public_key = identity._private_key.public_key()
    company = TokenVerifier(ISSUER, AUDIENCE, key_resolver=lambda _token: public_key)
    with pytest.raises(AuthError):
        company.verify(identity.issue("admin"))


def test_dev_issuer_and_audience_are_not_real_hosts():
    assert DEV_ISSUER.endswith(".invalid")
    assert DEV_AUDIENCE != AUDIENCE
