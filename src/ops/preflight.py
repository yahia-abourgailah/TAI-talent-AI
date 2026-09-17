"""Before anything serves a request: is this machine configured to? (week 8)

    python -m ops.preflight                               # the environment as it is
    python -m ops.preflight --env-file /etc/talent/talent.env
    python -m ops.preflight --strict                      # warnings fail too

Reads the configuration and says, in one list, what is missing or dangerous. Exit 0: nothing
stops the start (warnings may be listed). Exit 2: at least one problem that must be fixed first.
With --strict, warnings exit 1. The deploy script runs it before touching anything, and the
production compose file runs it before the API and the workers start.

It prints setting names and what is wrong with them, never a value: a secret must not reach a
terminal log because it was wrong.
"""

import argparse
import os
import re
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from pydantic import ValidationError

from config import Environment, Settings
from config.settings import AuthMode, OcrMode

ERROR, WARN, OK = "error", "warning", "ok"

# Values that are placeholders, published examples or well-known defaults. Compared lower-case.
EXAMPLE_VALUES = frozenset(
    {
        "",
        "changeme",
        "change-me",
        "change_me",
        "password",
        "postgres",
        "secret",
        "minioadmin",
        "access",
        "secret-key-value",
        "hunter2",
        "example",
        "xxx",
        "todo",
    }
)
SECRETS = (
    "TALENT_DB_OWNER_PASSWORD",
    "TALENT_DB_APP_PASSWORD",
    "TALENT_BLOB_ACCESS_KEY",
    "TALENT_BLOB_SECRET_KEY",
    "TALENT_JWT_SECRET",
)
# Accepted by the settings model when empty, but nothing works without them.
REQUIRED = ("TALENT_DB_DSN", "TALENT_REDIS_URL", "TALENT_BLOB_ENDPOINT")
MIN_SECRET_LENGTH = 16
MIN_JWT_SECRET_LENGTH = 32
# Settings nothing reads any more. Left in a file, they only mislead the next person.
UNUSED = ("TALENT_EVAL_PATH", "TALENT_VECTOR_URL")
# The platform calls no language model (CR-01). An address for one is a door nobody needs.
MODEL_HOSTS = ("TALENT_LLM_BASE_URL", "TALENT_EMBED_BASE_URL")
LOOPBACK = {"127.0.0.1", "localhost", "::1"}


@dataclass(frozen=True, slots=True)
class Finding:
    level: str
    setting: str
    message: str


def read_env_file(path: Path) -> dict[str, str]:
    """KEY=VALUE lines. Comments and blank lines are skipped; quotes around a value are removed."""
    values: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip().removeprefix("export ").strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
            value = value[1:-1]
        values[key] = value
    return values


def _settings(env: Mapping[str, str]) -> tuple[Settings | None, list[Finding]]:
    fields: dict[str, Any] = {
        name.removeprefix("TALENT_").lower(): value
        for name, value in env.items()
        if name.startswith("TALENT_")
    }
    try:
        return Settings(_env_file=None, **fields), []
    except ValidationError as exc:
        findings = []
        for error in exc.errors():
            where = ".".join(str(part) for part in error["loc"])
            message = str(error["msg"]).removeprefix("Value error, ")
            # A rule across settings has no field of its own; its message names the setting.
            named = re.search(r"TALENT_[A-Z_]+", message)
            setting = (
                f"TALENT_{where.upper()}" if where else named.group(0) if named else "(settings)"
            )
            if error["type"] == "missing":
                message = "is not set"
            findings.append(Finding(ERROR, setting, message))
        return None, findings


def _https_origin(origin: str) -> str | None:
    """What is wrong with an allowed browser origin, or None."""
    if origin == "*" or "*" in origin:
        return "a wildcard lets any website call the API"
    parts = urlsplit(origin)
    if parts.scheme != "https" or not parts.hostname:
        return "must be an https origin, such as https://careers.example.com"
    if parts.path not in ("", "/") or parts.query or parts.fragment:
        return "must be an origin only: no path, query or fragment"
    return None


def check(env: Mapping[str, str], *, api_bind: str | None = None) -> list[Finding]:
    """Every finding for this configuration. `api_bind` is the address the API is published on,
    when known, so the proxy count can be checked against the deployment."""
    settings, findings = _settings(env)
    deployed = env.get("TALENT_ENV", "") in (Environment.STAGING, Environment.PROD)
    live = ERROR if deployed else WARN

    if settings is not None:
        if settings.auth_mode is AuthMode.OIDC:
            issuer = urlsplit(settings.oidc_issuer)
            if issuer.scheme != "https":
                findings.append(Finding(live, "TALENT_OIDC_ISSUER", "must be an https URL"))
        if settings.ocr_mode_in_force is OcrMode.API:
            if not settings.ocr_base_url:
                findings.append(
                    Finding(
                        live,
                        "TALENT_OCR_BASE_URL",
                        "is not set: CV upload cannot work. Set it, or launch with CV upload "
                        "closed and say so",
                    )
                )
            if not settings.ocr_api_key.get_secret_value():
                findings.append(Finding(live, "TALENT_OCR_API_KEY", "is not set"))

        origins = settings.cors_origin_list
        if not origins:
            findings.append(
                Finding(
                    live,
                    "TALENT_CORS_ORIGINS",
                    "is empty: the careers page cannot call the API from a browser",
                )
            )
        for origin in origins:
            problem = _https_origin(origin)
            if problem and not (not deployed and urlsplit(origin).hostname in LOOPBACK):
                findings.append(Finding(ERROR, "TALENT_CORS_ORIGINS", f"{origin}: {problem}"))

        if not settings.backup_dir:
            # Inside the containers this is empty on purpose: the backups are the host's, and the
            # host's watch and the deploy script check them.
            findings.append(
                Finding(
                    WARN,
                    "TALENT_BACKUP_DIR",
                    "is not set here: this process cannot say whether last night's backup ran",
                )
            )
        if not settings.crm_webhook_url:
            findings.append(
                Finding(WARN, "TALENT_CRM_WEBHOOK_URL", "is not set: events wait in the feed")
            )

        hops = settings.trusted_proxy_hops
        if api_bind is not None:
            host = api_bind.strip().strip("[]")  # an address, without a port
            behind_proxy = host in LOOPBACK
            if behind_proxy and hops == 0 and deployed:
                findings.append(
                    Finding(
                        ERROR,
                        "TALENT_TRUSTED_PROXY_HOPS",
                        "is 0, but the API is published on loopback, so a proxy is in front of "
                        "it: every candidate would share the proxy's rate limit. Set it to the "
                        "number of proxies",
                    )
                )
            if not behind_proxy and hops > 0:
                findings.append(
                    Finding(
                        ERROR,
                        "TALENT_TRUSTED_PROXY_HOPS",
                        f"is {hops}, but the API is published directly on {host}: anyone could "
                        "choose their own address with X-Forwarded-For. Set it to 0",
                    )
                )

    for name in REQUIRED:
        if name in env and not env[name].strip():
            findings.append(Finding(live, name, "is empty"))
    for name in SECRETS:
        value = env.get(name, "")
        if value.strip().lower() in EXAMPLE_VALUES:
            findings.append(Finding(live, name, "is empty or still an example value"))
        else:
            wanted = MIN_JWT_SECRET_LENGTH if name == "TALENT_JWT_SECRET" else MIN_SECRET_LENGTH
            if len(value) < wanted:
                findings.append(Finding(live, name, f"is shorter than {wanted} characters"))
    for name in ("TALENT_ALERT_WEBHOOK_URL",):
        if not env.get(name):
            findings.append(Finding(WARN, name, "is not set: a failing check will tell nobody"))
    for name in UNUSED:
        if name in env:
            findings.append(Finding(WARN, name, "is not used by anything; remove it"))
    for name in MODEL_HOSTS:
        if env.get(name):
            findings.append(
                Finding(
                    WARN,
                    name,
                    "is set, but the platform calls no language model (CR-01); remove it",
                )
            )
    if not findings:
        findings.append(Finding(OK, "(all)", "nothing missing or dangerous"))
    return findings


def exit_code(findings: Sequence[Finding], strict: bool = False) -> int:
    levels = {f.level for f in findings}
    if ERROR in levels:
        return 2
    if strict and WARN in levels:
        return 1
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m ops.preflight", description=__doc__)
    parser.add_argument("--env-file", type=Path, help="read settings from this file")
    parser.add_argument("--strict", action="store_true", help="warnings fail too")
    parser.add_argument(
        "--api-bind",
        default=os.environ.get("TALENT_API_BIND"),
        help="where the API is published, e.g. 127.0.0.1 (default: $TALENT_API_BIND)",
    )
    args = parser.parse_args(argv)

    env = dict(os.environ)
    if args.env_file is not None:
        if not args.env_file.is_file():
            print(f"error: no file at {args.env_file}", file=sys.stderr)
            return 2
        env = {**read_env_file(args.env_file), **{k: v for k, v in env.items() if k in ("PATH",)}}
    findings = check(env, api_bind=args.api_bind)
    order = {ERROR: 0, WARN: 1, OK: 2}
    for finding in sorted(findings, key=lambda f: (order[f.level], f.setting)):
        print(f"{finding.level.upper():<8} {finding.setting:<28} {finding.message}")
    code = exit_code(findings, args.strict)
    errors = sum(f.level == ERROR for f in findings)
    warnings = sum(f.level == WARN for f in findings)
    verdict = "do not start" if code == 2 else "fix before go-live" if code else "may start"
    print(f"\n{errors} error(s), {warnings} warning(s): {verdict}.")
    return code


if __name__ == "__main__":
    sys.exit(main())
