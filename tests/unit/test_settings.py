import pytest
from pydantic import ValidationError

from config import AuthMode, Environment


@pytest.mark.parametrize("env", ["staging", "prod"])
def test_fake_accounts_refused_outside_dev(make_settings, env):
    with pytest.raises(ValidationError, match="Fake accounts only run in dev"):
        make_settings(env=env, auth_mode="dev")


def test_fake_accounts_allowed_in_dev_without_identity_provider(make_settings):
    settings = make_settings(auth_mode="dev", oidc_issuer="", oidc_audience="")
    assert settings.auth_mode is AuthMode.DEV
    assert settings.env is Environment.DEV


def test_company_sign_in_is_the_default(make_settings):
    assert make_settings().auth_mode is AuthMode.OIDC


def test_identity_provider_required_for_company_sign_in(make_settings):
    with pytest.raises(ValidationError, match="TALENT_OIDC_ISSUER"):
        make_settings(env="prod", oidc_issuer="")


def test_environment_has_no_default(make_settings):
    with pytest.raises(ValidationError, match="env"):
        make_settings(env=None)


def test_secrets_never_appear_in_repr(make_settings):
    shown = repr(make_settings())
    assert "hunter2" not in shown
    assert "secret-key-value" not in shown
