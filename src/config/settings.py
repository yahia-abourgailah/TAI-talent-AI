"""Runtime configuration, read from TALENT_* environment variables.

No secret and no data location has a usable default: an unset value stops the process at
startup instead of falling back to something committed (docs/DATA_HANDLING.md, RSK-12).
"""

from enum import StrEnum
from functools import lru_cache
from typing import Self
from urllib.parse import urlsplit

from pydantic import SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Environment(StrEnum):
    DEV = "dev"
    STAGING = "staging"
    PROD = "prod"


class AuthMode(StrEnum):
    OIDC = "oidc"  # the company identity provider
    DEV = "dev"  # fake accounts signed locally, dev only


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

    # Events to the CRM (API plan section 7). Delivery is off while the URL is empty.
    crm_webhook_url: str = ""
    crm_webhook_secret: SecretStr = SecretStr("")

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
