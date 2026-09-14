import pytest
from pydantic import ValidationError

from config import Environment


@pytest.mark.parametrize("env", ["staging", "prod"])
def test_login_bypass_refused_outside_dev(make_settings, env):
    with pytest.raises(ValidationError, match="only runs in dev"):
        make_settings(env=env, auth_dev_bypass=True)


def test_login_bypass_allowed_in_dev_without_identity_provider(make_settings):
    settings = make_settings(auth_dev_bypass=True, oidc_issuer="", oidc_audience="")
    assert settings.auth_dev_bypass
    assert settings.env is Environment.DEV


def test_identity_provider_required_without_bypass(make_settings):
    with pytest.raises(ValidationError, match="TALENT_OIDC_ISSUER"):
        make_settings(env="prod", oidc_issuer="")


def test_environment_has_no_default(make_settings):
    with pytest.raises(ValidationError, match="env"):
        make_settings(env=None)


def test_secrets_never_appear_in_repr(make_settings):
    shown = repr(make_settings())
    assert "hunter2" not in shown
    assert "secret-key-value" not in shown
