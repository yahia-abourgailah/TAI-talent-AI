"""Runtime configuration, read from TALENT_* environment variables.

No secret and no data location has a usable default: an unset value stops the process at
startup instead of falling back to something committed (docs/DATA_HANDLING.md, RSK-12).
"""

from enum import StrEnum
from functools import lru_cache
from typing import Self
from urllib.parse import urlsplit

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Environment(StrEnum):
    DEV = "dev"
    STAGING = "staging"
    PROD = "prod"


class AuthMode(StrEnum):
    OIDC = "oidc"  # the company identity provider
    DEV = "dev"  # fake accounts signed locally, dev only


class OcrMode(StrEnum):
    API = "api"  # the company OCR API, on our own host (CR-01)
    FAKE = "fake"  # saved sample answers, dev and tests only


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="TALENT_", env_file=".env", extra="ignore")

    env: Environment

    db_dsn: SecretStr
    redis_url: SecretStr
    blob_endpoint: str
    blob_bucket: str = "talent-raw"
    blob_access_key: SecretStr
    blob_secret_key: SecretStr

    auth_mode: AuthMode = AuthMode.OIDC
    oidc_issuer: str = ""
    oidc_audience: str = ""

    log_level: str = "INFO"

    # Where backups are kept, so the health checks can say whether last night's ran (NFR-02).
    # Empty means backups are not watched from the API.
    backup_dir: str = ""

    # The browsers allowed to call us: the careers page and the CRM dashboard, as exact origins
    # ("https://careers.theaddress.com"), comma-separated. Empty means no browser on another
    # origin can call the API at all, which is the right default (D-WEB-2).
    cors_origins: str = ""

    # How many proxies sit in front of the API. Rate limits are per candidate, so the address must
    # be the candidate's: with 0, X-Forwarded-For is ignored entirely, because anyone can send it.
    trusted_proxy_hops: int = Field(default=0, ge=0, le=5)

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]

    # Where the careers page lives, so a job post comes back as a link somebody can paste into a
    # post (BR-602). The website team owns that page; the platform only needs its address. Empty
    # means a job post still has its code, and the link is left to whoever publishes it.
    careers_url: str = ""

    # Events to the CRM (API plan section 7). Delivery is off while the URL is empty.
    crm_webhook_url: str = ""
    crm_webhook_secret: SecretStr = SecretStr("")

    # Reading CVs (NFR-01, NFR-04). Unset means the fake OCR in dev and the real API elsewhere.
    # The limits are safe defaults until the OCR team gives its real numbers.
    ocr_mode: OcrMode | None = None
    ocr_base_url: str = ""
    ocr_api_key: SecretStr = SecretStr("")
    ocr_timeout_seconds: float = Field(default=60.0, gt=0, le=600)
    ocr_max_concurrency: int = Field(default=2, ge=1, le=32)
    ocr_max_attempts: int = Field(default=4, ge=1, le=10)

    @property
    def ocr_mode_in_force(self) -> OcrMode:
        if self.ocr_mode is not None:
            return self.ocr_mode
        return OcrMode.FAKE if self.env is Environment.DEV else OcrMode.API

    @model_validator(mode="after")
    def _check_careers_url(self) -> Self:
        if self.careers_url:
            parts = urlsplit(self.careers_url)
            if parts.scheme not in {"http", "https"} or not parts.hostname:
                raise ValueError("TALENT_CAREERS_URL must be an http or https URL.")
        return self

    @model_validator(mode="after")
    def _check_ocr(self) -> Self:
        if self.ocr_mode is OcrMode.FAKE and self.env is not Environment.DEV:
            raise ValueError(
                f"TALENT_OCR_MODE=fake is set with TALENT_ENV={self.env}. The fake OCR only runs "
                "in dev and tests; set TALENT_OCR_MODE=api and TALENT_OCR_BASE_URL."
            )
        if self.ocr_base_url:
            parts = urlsplit(self.ocr_base_url)
            if parts.scheme not in {"http", "https"} or not parts.hostname:
                raise ValueError("TALENT_OCR_BASE_URL must be an http or https URL.")
        return self

    @model_validator(mode="after")
    def _check_sign_in(self) -> Self:
        if self.auth_mode is AuthMode.DEV:
            if self.env is not Environment.DEV:
                raise ValueError(
                    f"TALENT_AUTH_MODE=dev is set with TALENT_ENV={self.env}. "
                    "Fake accounts only run in dev; set TALENT_AUTH_MODE=oidc."
                )
        elif not (self.oidc_issuer and self.oidc_audience):
            raise ValueError(
                "Set TALENT_OIDC_ISSUER and TALENT_OIDC_AUDIENCE to the company identity "
                "provider, or set TALENT_AUTH_MODE=dev on a dev machine."
            )
        return self

    @model_validator(mode="after")
    def _check_webhook(self) -> Self:
        if not self.crm_webhook_url:
            return self
        parts = urlsplit(self.crm_webhook_url)
        local = parts.scheme == "http" and parts.hostname in {"localhost", "127.0.0.1"}
        if parts.scheme != "https" and not (local and self.env is Environment.DEV):
            raise ValueError(
                "TALENT_CRM_WEBHOOK_URL must use https. Plain http is allowed only to localhost "
                "in dev."
            )
        if len(self.crm_webhook_secret.get_secret_value()) < 32:
            raise ValueError(
                "Set TALENT_CRM_WEBHOOK_SECRET to at least 32 characters when "
                "TALENT_CRM_WEBHOOK_URL is set."
            )
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
