"""What a machine must have before it serves anyone (week 8). No database, no network."""

import pytest

from config import Settings
from ops.preflight import (
    CRITICAL,
    OK,
    WARN,
    behind_a_proxy,
    careers_page,
    reading_cvs,
    secrets_set,
    sign_in,
)

GOOD_SECRET = "d3f9" * 9  # 36 characters, nothing like an example


def settings(**extra) -> Settings:
    base = {
        "_env_file": None,
        "env": "prod",
        "auth_mode": "oidc",
        "oidc_issuer": "https://login.example.com/",
        "oidc_audience": "talent-platform",
        "db_dsn": f"postgresql+psycopg://talent_app:{GOOD_SECRET}@db.internal:5432/talent",
        "redis_url": "redis://cache.internal:6379/0",
        "blob_endpoint": "http://storage.internal:9000",
        "blob_access_key": GOOD_SECRET,
        "blob_secret_key": GOOD_SECRET,
        "ocr_mode": "api",
        "ocr_base_url": "https://ocr.internal",
        "ocr_api_key": GOOD_SECRET,
    }
    return Settings(**{**base, **extra})


def test_a_careers_page_that_cannot_call_us_is_critical_in_production():
    check = careers_page(settings())
    assert check.status == CRITICAL
    assert "TALENT_CORS_ORIGINS" in check.detail
    assert careers_page(settings(env="dev", auth_mode="dev")).status == WARN


def test_origins_must_be_exact_origins():
    assert careers_page(settings(cors_origins="https://careers.example.com")).status == OK
    for wrong in ("careers.example.com", "https://careers.example.com/apply", "*"):
        assert careers_page(settings(cors_origins=wrong)).status == CRITICAL


def test_plain_http_origins_are_refused_outside_development():
    assert careers_page(settings(cors_origins="http://careers.example.com")).status == CRITICAL
    dev = settings(env="dev", auth_mode="dev", cors_origins="http://localhost:3000")
    assert careers_page(dev).status == OK


def test_a_load_balancer_that_is_not_declared_is_a_warning():
    assert behind_a_proxy(settings()).status == WARN
    assert behind_a_proxy(settings(trusted_proxy_hops=1)).status == OK


@pytest.mark.parametrize("placeholder", ["", "changeme", "secret", "short"])
def test_a_secret_that_is_still_an_example_is_refused(monkeypatch, placeholder):
    monkeypatch.setenv("TALENT_JWT_SECRET", placeholder)
    check = secrets_set(settings())
    assert check.status == CRITICAL
    assert "TALENT_JWT_SECRET" in check.detail


def test_real_secrets_pass(monkeypatch):
    monkeypatch.setenv("TALENT_JWT_SECRET", GOOD_SECRET)
    assert secrets_set(settings()).status == OK


def test_the_identity_provider_must_be_named_and_https():
    assert sign_in(settings()).status == OK
    # An unnamed provider never gets this far: Settings refuses to build at all.
    with pytest.raises(ValueError, match="TALENT_OIDC_ISSUER"):
        settings(oidc_issuer="", oidc_audience="")
    # A named one over plain http does, and should not.
    assert sign_in(settings(oidc_issuer="http://login.example.com")).status == CRITICAL


def test_the_real_reader_with_no_address_reads_nothing():
    assert reading_cvs(settings()).status == OK
    assert reading_cvs(settings(ocr_base_url="")).status == CRITICAL
    # No key is only right if the reader has none, so it is worth saying out loud.
    assert reading_cvs(settings(ocr_api_key="")).status == WARN


def test_the_stand_in_reader_cannot_even_be_configured_in_production():
    with pytest.raises(ValueError, match="fake"):
        settings(ocr_mode="fake")


def test_the_development_bypass_cannot_be_configured_in_production():
    with pytest.raises(ValueError, match="dev"):
        settings(auth_mode="dev")
