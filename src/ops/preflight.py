"""Is this machine configured to run the platform? (week 8)

    python -m ops.preflight                 # everything it can check from here
    python -m ops.preflight --no-db         # configuration only, before the database exists
    python -m ops.preflight --json

Exit 0 fine, 1 look at this, 2 do not start. Run it on a new machine before the first deploy, and
after every change to the environment file: finding six problems at once beats finding them one
failed request at a time, after candidates have started applying.

Some of these already refuse at start-up — the developer sign-in bypass outside development, the
stand-in CV reader outside development — and refusing is right. This gathers them into one answer a
person can read, and adds the ones nothing else would notice: an empty careers-page origin, a proxy
count that does not match the deployment, a secret still at its example value, backups nobody
configured.
"""

import argparse
import json
import os
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from config import Environment, Settings
from ops.watch import CRITICAL, OK, RANK, WARN, Check, backup_freshness, overall

EXIT_CODE = {OK: 0, WARN: 1, CRITICAL: 2}
# Values that mean "nobody has set this yet", whatever they are set to.
PLACEHOLDERS = {"", "unused", "changeme", "change-me", "secret", "password", "example", "todo"}
MINIMUM_SECRET = 32


def _worse(first: str, second: str) -> str:
    return max(first, second, key=lambda name: RANK[name])


def _is_real_secret(value: str) -> bool:
    return value.strip().lower() not in PLACEHOLDERS and len(value.strip()) >= MINIMUM_SECRET


def sign_in(settings: Settings) -> Check:
    """Staff sign in with company accounts, everywhere but a developer's machine."""
    if settings.auth_mode.value == "dev":
        # Settings refuses this outside development, so reaching here means we are in development.
        return Check("sign_in", WARN, "fake development accounts (development only)")
    missing = [
        name
        for name, value in (
            ("TALENT_OIDC_ISSUER", settings.oidc_issuer),
            ("TALENT_OIDC_AUDIENCE", settings.oidc_audience),
        )
        if not value.strip()
    ]
    if missing:
        return Check(
            "sign_in", CRITICAL, f"company sign-in is not configured: {', '.join(missing)}"
        )
    if urlsplit(settings.oidc_issuer).scheme != "https":
        return Check("sign_in", CRITICAL, "the identity provider must be an https address")
    return Check("sign_in", OK, f"company accounts through {urlsplit(settings.oidc_issuer).netloc}")


def reading_cvs(settings: Settings) -> Check:
    """The CV reader. The stand-in is refused outside development by Settings itself."""
    mode = settings.ocr_mode_in_force.value
    if mode == "fake":
        return Check("reading_cvs", WARN, "the stand-in reader, with saved answers (development)")
    if not settings.ocr_base_url.strip():
        return Check(
            "reading_cvs",
            CRITICAL,
            "the real reader is in force but TALENT_OCR_BASE_URL is empty: no CV can be read",
        )
    if not settings.ocr_api_key.get_secret_value().strip():
        return Check(
            "reading_cvs", WARN, "no TALENT_OCR_API_KEY: only right if the reader has none"
        )
    return Check("reading_cvs", OK, f"the reader at {urlsplit(settings.ocr_base_url).netloc}")


def careers_page(settings: Settings) -> Check:
    """Which browsers may call us. Empty means the careers page cannot, and will fail on day one."""
    origins = settings.cors_origin_list
    if not origins:
        level = CRITICAL if settings.env is not Environment.DEV else WARN
        return Check(
            "careers_page",
            level,
            "TALENT_CORS_ORIGINS is empty: no browser on another origin can call the API",
        )
    wrong = [
        origin
        for origin in origins
        if urlsplit(origin).scheme not in {"http", "https"}
        or not urlsplit(origin).netloc
        or urlsplit(origin).path
    ]
    if wrong:
        return Check(
            "careers_page",
            CRITICAL,
            f"these are not exact origins (scheme and host, no path): {', '.join(wrong)}",
        )
    insecure = [origin for origin in origins if urlsplit(origin).scheme == "http"]
    if insecure and settings.env is not Environment.DEV:
        return Check(
            "careers_page", CRITICAL, f"plain http origins outside development: {insecure}"
        )
    return Check("careers_page", OK, f"{len(origins)} origin(s) allowed: {', '.join(origins)}")


def behind_a_proxy(settings: Settings) -> Check:
    """Rate limits are per candidate, so the proxy count has to match the deployment."""
    hops = settings.trusted_proxy_hops
    if hops == 0 and settings.env is not Environment.DEV:
        return Check(
            "behind_a_proxy",
            WARN,
            "TALENT_TRUSTED_PROXY_HOPS is 0: right only if nothing sits in front of the API. "
            "Behind a load balancer every candidate counts as one address",
            {"hops": hops},
        )
    return Check("behind_a_proxy", OK, f"{hops} proxy hop(s) trusted", {"hops": hops})


def secrets_set(settings: Settings) -> Check:
    """Secrets that are still an example are worse than secrets that are missing."""
    weak = []
    for name, value in (
        ("TALENT_JWT_SECRET", os.environ.get("TALENT_JWT_SECRET", "")),
        ("TALENT_BLOB_SECRET_KEY", settings.blob_secret_key.get_secret_value()),
        ("TALENT_DB_DSN", settings.db_dsn.get_secret_value()),
    ):
        if not _is_real_secret(value):
            weak.append(name)
    if settings.crm_webhook_url and not _is_real_secret(
        settings.crm_webhook_secret.get_secret_value()
    ):
        weak.append("TALENT_CRM_WEBHOOK_SECRET")
    if weak:
        level = CRITICAL if settings.env is not Environment.DEV else WARN
        return Check("secrets", level, f"missing, too short or still an example: {', '.join(weak)}")
    return Check("secrets", OK, "set, and none of them an example value")


def backups_configured(settings: Settings) -> Check:
    """Where backups go, and whether last night's happened."""
    directory = settings.backup_dir or os.environ.get("TALENT_BACKUP_DIR", "")
    if not directory:
        level = CRITICAL if settings.env is not Environment.DEV else WARN
        return Check("backups", level, "TALENT_BACKUP_DIR is not set: nothing is being backed up")
    path = Path(directory).expanduser()
    if not path.exists():
        return Check("backups", CRITICAL, f"{path} does not exist")
    if not os.access(path, os.W_OK):
        return Check("backups", CRITICAL, f"{path} cannot be written to by this user")
    return backup_freshness(path)


def alerts_go_somewhere(settings: Settings) -> Check:
    if os.environ.get("TALENT_ALERT_WEBHOOK_URL", "").strip():
        return Check("alerts", OK, "a health alert reaches the chat channel")
    level = WARN if settings.env is Environment.DEV else CRITICAL
    return Check(
        "alerts",
        level,
        "TALENT_ALERT_WEBHOOK_URL is not set: nothing tells anyone when something breaks",
    )


def erasure_ready(settings: Settings) -> Check:
    """Erasure runs as the owner, and only under a policy Legal has activated."""
    if not os.environ.get("TALENT_ERASURE_DSN") and not os.environ.get("TALENT_DB_MIGRATION_DSN"):
        return Check(
            "erasure", WARN, "no owner DSN set, so nothing can be erased when it falls due"
        )
    return Check("erasure", OK, "the owner DSN is set; a policy still has to be in force (CR-03)")


def _database_checks(settings: Settings) -> list[Check]:
    """Two different questions, asked as two different roles.

    Can the application read what it needs? That is asked as the application, because a database
    that is up and a database the application can read are not the same thing — a restore with no
    grants looks exactly like a healthy one from the outside.

    Is the schema the one this code expects? That is asked as the owner, because the application
    has no business reading the migration table, and does not have the grant to.
    """
    from alembic.config import Config
    from alembic.script import ScriptDirectory
    from sqlalchemy import create_engine, text

    checks: list[Check] = []
    try:
        engine = create_engine(settings.db_dsn.get_secret_value(), pool_pre_ping=True)
        with engine.connect() as conn:
            candidates = conn.execute(text("SELECT count(*) FROM core.candidate")).scalar_one()
            checks.append(
                Check(
                    "database",
                    OK,
                    f"the application can read it: {int(candidates):,} candidate(s)",
                    {"candidates": int(candidates)},
                )
            )
    except Exception as exc:
        checks.append(
            Check(
                "database",
                CRITICAL,
                "the application cannot read it "
                f"({type(exc).__name__}). A restore with no grants looks like this",
            )
        )
        return checks

    owner_dsn = os.environ.get("TALENT_ERASURE_DSN") or os.environ.get("TALENT_DB_MIGRATION_DSN")
    head = ScriptDirectory.from_config(Config("alembic.ini")).get_current_head()
    if not owner_dsn:
        checks.append(
            Check("schema", WARN, f"not checked: no owner DSN here. The code expects {head}")
        )
        return checks
    try:
        owner = create_engine(owner_dsn, pool_pre_ping=True)
        with owner.connect() as conn:
            at = conn.execute(text("SELECT max(version_num) FROM alembic_version")).scalar_one()
            checks.append(
                Check(
                    "schema",
                    OK if at == head else CRITICAL,
                    f"migrated to {at}"
                    + ("" if at == head else f", but this code expects {head}: run the migrations"),
                    {"at": at, "head": head},
                )
            )
            for name, query, missing in (
                (
                    "step_list",
                    "SELECT pipeline.active_list()",
                    "no step list is in force: nobody can be moved through the pipeline",
                ),
                (
                    "consent_wording",
                    "SELECT core.consent_wording_in_force()",
                    "no consent wording is in force: nobody can apply",
                ),
                (
                    "retention",
                    "SELECT core.retention_in_force()",
                    "no retention policy is in force: nothing will ever be erased (OPN-07)",
                ),
            ):
                value = conn.execute(text(query)).scalar_one_or_none()
                checks.append(
                    Check(name, OK, str(value))
                    if value
                    else Check(name, WARN if name == "retention" else CRITICAL, missing)
                )
    except Exception as exc:
        checks.append(
            Check("schema", CRITICAL, f"cannot be read as the owner: {type(exc).__name__}")
        )
    return checks


def run_checks(settings: Settings, *, with_database: bool = True) -> list[Check]:
    checks = [
        Check("environment", OK, f"TALENT_ENV={settings.env.value}"),
        sign_in(settings),
        reading_cvs(settings),
        careers_page(settings),
        behind_a_proxy(settings),
        secrets_set(settings),
        backups_configured(settings),
        alerts_go_somewhere(settings),
        erasure_ready(settings),
    ]
    if with_database:
        checks += _database_checks(settings)
    return checks


def report(checks: Sequence[Check]) -> dict[str, Any]:
    return {
        "status": overall(checks),
        "checks": [check.as_dict() for check in checks],
        "runbook": "docs/ops/RUNBOOK.md",
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m ops.preflight", description=__doc__.splitlines()[0]
    )
    parser.add_argument("--no-db", action="store_true", help="configuration only")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    try:
        settings = Settings()
    except Exception as exc:
        # Settings refuses some combinations outright. That refusal is the most important answer
        # this command can give, so print it as one rather than as a stack trace.
        refusal = str(exc).replace("\n", " ")
        if args.json:
            print(json.dumps({"status": CRITICAL, "checks": [], "refused": refusal}, indent=2))
        else:
            print(f"CRIT  configuration  the platform refuses to start with this: {refusal}")
        return EXIT_CODE[CRITICAL]

    checks = run_checks(settings, with_database=not args.no_db)
    if args.json:
        print(json.dumps(report(checks), indent=2))
    else:
        width = max(len(check.name) for check in checks)
        for check in checks:
            mark = {OK: "ok  ", WARN: "WARN", CRITICAL: "CRIT"}[check.status]
            print(f"{mark}  {check.name.ljust(width)}  {check.detail}")
        answer = overall(checks)
        print(
            f"\n{answer}."
            + ("" if answer == OK else " Fix what is marked before this machine serves anyone.")
        )
    return EXIT_CODE[overall(checks)]


if __name__ == "__main__":
    sys.exit(main())
