"""Backups, and restoring one for real (week 7: NFR-02).

    python -m ops.backup take    --out DIR [--keep 14]
    python -m ops.backup verify  --out DIR [--backup FILE]
    python -m ops.backup files   --out DIR
    python -m ops.backup list    --out DIR
    python -m ops.backup drill   --out DIR [--backup FILE]   # into an empty container (week 8)

A backup nobody has restored is not a backup, it is a file. `take` writes the dump and then
restores it into a throwaway database and counts the rows back, every time. A dump that cannot be
restored is reported as a failure on the spot, not discovered on the day it is needed.

The dump holds every candidate's name, number and history, so it lives outside any git repository,
owner-readable only, exactly like the master workbook (docs/DATA_HANDLING.md). The CV files
themselves are not in the database; `files` copies them from the object store, and because their
keys are content hashes a file already copied is never copied again.

pg_dump and pg_restore must match the server, so they are run wherever the server is:
`--tools local` uses the ones on PATH, `--tools docker:postgres` runs them inside that compose
service, which is how the development stack works.
"""

import argparse
import hashlib
import json
import os
import re
import secrets
import shlex
import subprocess
import sys
import time
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from importer.paths import DataLocationError, check_outside_repository

DUMP_FORMAT = "custom"
FILE_PATTERN = "talent-%Y%m%dT%H%M%SZ"
COUNTED_SCHEMAS = ("raw", "core", "pipeline", "intake", "integration")
KEEP_DEFAULT = 14
# Restoring over a live database is how a good backup ruins an afternoon. The check restores into a
# database of its own and drops it again.
SCRATCH_PREFIX = "talent_verify_"
# A restore is only good if the application can read what came back, so every check counts the
# grants the app role holds. Zero means the data is there and the platform is dead.
_GRANT_COUNT = (
    "SELECT count(*) FROM information_schema.role_table_grants WHERE grantee = 'talent_rw'"
)


class BackupError(Exception):
    """The backup or its check could not be completed. The message says what to do."""


def _timestamp() -> str:
    return datetime.now(UTC).strftime(FILE_PATTERN)


class Tools:
    """Where pg_dump, pg_restore and psql live: on this machine, or inside a container."""

    def __init__(self, where: str = "local") -> None:
        self.where = where
        if where == "local":
            self._prefix: list[str] = []
        elif where.startswith("docker:"):
            service = where.split(":", 1)[1]
            if not service:
                raise BackupError("--tools docker: needs a service, e.g. docker:postgres.")
            self._prefix = ["docker", "compose", "exec", "-T", service]
        else:
            raise BackupError("--tools is 'local' or 'docker:<compose service>'.")

    def command(self, program: str, *arguments: str) -> list[str]:
        return [*self._prefix, program, *arguments]

    def run(
        self, program: str, *arguments: str, stdin: bytes | None = None, password: str = ""
    ) -> bytes:
        environment = dict(os.environ)
        if password:
            environment["PGPASSWORD"] = password
        command = self.command(program, *arguments)
        if self._prefix and password:
            # docker compose exec does not carry the caller's environment into the container.
            command = [*self._prefix[:-1], "-e", "PGPASSWORD", self._prefix[-1], program]
            command += list(arguments)
        # The programs are fixed and every argument is built here, never taken from a request.
        finished = subprocess.run(
            command, input=stdin, capture_output=True, env=environment, check=False
        )
        if finished.returncode != 0:
            message = finished.stderr.decode("utf-8", "replace").strip().splitlines()
            tail = message[-1] if message else f"exit {finished.returncode}"
            raise BackupError(f"{program} failed: {tail}\n  ran: {shlex.join(command)}")
        return finished.stdout


class Server:
    """The database to back up, as pg_dump needs it: host, port, user, password, database."""

    def __init__(self, dsn: str) -> None:
        parts = urlsplit(dsn.replace("postgresql+psycopg://", "postgresql://"))
        if parts.scheme not in {"postgresql", "postgres"} or not parts.hostname:
            raise BackupError("The DSN must be a postgresql:// URL.")
        self.host = parts.hostname
        self.port = str(parts.port or 5432)
        self.user = parts.username or ""
        self.password = parts.password or ""
        self.database = (parts.path or "/").lstrip("/") or "postgres"

    def where(self, inside_container: bool) -> list[str]:
        """Connection arguments. Inside the container the server is local, not on the host."""
        host = "127.0.0.1" if inside_container else self.host
        return [
            "--host",
            host,
            "--port",
            self.port if not inside_container else "5432",
            "--username",
            self.user,
        ]


def _connection(server: Server, tools: Tools) -> list[str]:
    return server.where(inside_container=tools.where.startswith("docker:"))


def row_counts(server: Server, tools: Tools, database: str | None = None) -> dict[str, int]:
    """Rows per table in the schemas that hold our data, so a restore can be counted back."""
    schemas = ", ".join(f"'{name}'" for name in COUNTED_SCHEMAS)
    query = (
        "SELECT format('%I.%I', schemaname, relname) AS name, n_live_tup "
        f"FROM pg_stat_user_tables WHERE schemaname IN ({schemas}) ORDER BY name"
    )
    # n_live_tup is an estimate, so every table is counted for real.
    names = _psql(server, tools, database, query.replace(", n_live_tup", "")).splitlines()
    counts: dict[str, int] = {}
    for name in [line.strip() for line in names if line.strip()]:
        counts[name] = int(_psql(server, tools, database, f"SELECT count(*) FROM {name}").strip())
    return counts


def _psql(server: Server, tools: Tools, database: str | None, query: str) -> str:
    output = tools.run(
        "psql",
        *_connection(server, tools),
        "--dbname",
        database or server.database,
        "--no-align",
        "--tuples-only",
        "--command",
        query,
        password=server.password,
    )
    return output.decode("utf-8", "replace")


def alembic_revision(server: Server, tools: Tools, database: str | None = None) -> str:
    return _psql(server, tools, database, "SELECT version_num FROM alembic_version").strip()


def take(
    server: Server, tools: Tools, out: Path, *, keep: int = KEEP_DEFAULT, verify: bool = True
) -> dict[str, Any]:
    """Writes a dump, checks it by restoring it, and prunes the old ones."""
    out.mkdir(mode=0o700, parents=True, exist_ok=True)
    started = datetime.now(UTC)
    name = _timestamp()
    dump_path = out / f"{name}.dump"

    # With owner and privileges, on purpose. Without them the data restores and the platform is
    # still dead: every GRANT is missing, so the application role cannot read a single row. The
    # roles themselves are not in a database dump, so the restore procedure creates them first
    # (docs/ops/RUNBOOK.md §5), and check_fresh proves that procedure works.
    body = tools.run(
        "pg_dump",
        *_connection(server, tools),
        "--dbname",
        server.database,
        "--format",
        DUMP_FORMAT,
        "--no-owner",
        # The grants are kept: without them a restore on an empty machine holds every row and
        # the application may read none of them (found by the week 8 drill).
        password=server.password,
    )
    _write_private(dump_path, body)

    manifest: dict[str, Any] = {
        "file": dump_path.name,
        "database": server.database,
        "started_at": started.isoformat(),
        "finished_at": datetime.now(UTC).isoformat(),
        "bytes": len(body),
        "sha256": hashlib.sha256(body).hexdigest(),
        "alembic_revision": alembic_revision(server, tools),
        "row_counts": row_counts(server, tools),
        "restored": None,
    }
    if verify:
        manifest["restored"] = check(server, tools, dump_path, live_counts=manifest["row_counts"])
    _write_private(
        out / f"{name}.json", json.dumps(manifest, indent=2, sort_keys=True).encode("utf-8")
    )
    pruned = prune(out, keep)
    manifest["pruned"] = pruned
    return manifest


def check(
    server: Server,
    tools: Tools,
    dump_path: Path,
    *,
    live_counts: Mapping[str, int] | None = None,
) -> dict[str, Any]:
    """Restores the dump into a database of its own and counts the rows back."""
    scratch = SCRATCH_PREFIX + datetime.now(UTC).strftime("%Y%m%d%H%M%S")
    if not re.fullmatch(r"[a-z0-9_]+", scratch):  # pragma: no cover - the name is built here
        raise BackupError("The check database name is not a plain identifier.")
    started = datetime.now(UTC)
    body = dump_path.read_bytes()
    _psql(server, tools, "postgres", f'CREATE DATABASE "{scratch}"')
    try:
        tools.run(
            "pg_restore",
            *_connection(server, tools),
            "--dbname",
            scratch,
            "--no-owner",
            "--exit-on-error",
            stdin=body,
            password=server.password,
        )
        restored = row_counts(server, tools, scratch)
        revision = alembic_revision(server, tools, scratch)
        grants = int(_psql(server, tools, scratch, _GRANT_COUNT).strip() or 0)
    finally:
        _psql(server, tools, "postgres", f'DROP DATABASE IF EXISTS "{scratch}" WITH (FORCE)')

    live = dict(live_counts or row_counts(server, tools))
    missing = sorted(set(live) - set(restored))
    empty = sorted(name for name, rows in restored.items() if rows == 0 and live.get(name, 0) > 0)
    written_since = {
        name: live[name] - restored.get(name, 0)
        for name in live
        if live[name] > restored.get(name, 0)
    }
    result = {
        "restored_into": scratch,
        "grants_to_the_app_role": grants,
        "seconds": round((datetime.now(UTC) - started).total_seconds(), 1),
        "tables": len(restored),
        "rows": sum(restored.values()),
        "alembic_revision": revision,
        "tables_missing": missing,
        "tables_empty": empty,
        "rows_written_since_the_dump": written_since,
        "ok": not missing and not empty and grants > 0,
    }
    if not result["ok"]:
        raise BackupError(
            f"the dump restored into {scratch} is not complete: "
            f"missing {missing or 'none'}, empty {empty or 'none'}, "
            f"grants to the app role {grants}"
        )
    return result


def prune(out: Path, keep: int) -> list[str]:
    """Keeps the newest `keep` backups. Never removes the only one we have."""
    if keep < 1:
        raise BackupError("--keep is at least 1: a backup store with nothing in it is not one.")
    dumps = sorted(out.glob("talent-*.dump"), reverse=True)
    removed = []
    for old in dumps[keep:]:
        old.unlink()
        old.with_suffix(".json").unlink(missing_ok=True)
        removed.append(old.name)
    return removed


def listed(out: Path) -> list[dict[str, Any]]:
    """Every backup in the directory, newest first, with what its manifest says."""
    rows = []
    for dump in sorted(out.glob("talent-*.dump"), reverse=True):
        manifest_path = dump.with_suffix(".json")
        manifest: dict[str, Any] = {}
        if manifest_path.exists():
            try:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                manifest = {"unreadable_manifest": True}
        rows.append(
            {
                "file": dump.name,
                "bytes": dump.stat().st_size,
                "alembic_revision": manifest.get("alembic_revision"),
                "restored": bool((manifest.get("restored") or {}).get("ok")),
                "rows": sum((manifest.get("row_counts") or {}).values()) or None,
            }
        )
    return rows


def _write_private(path: Path, body: bytes) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "wb") as handle:
        handle.write(body)


def copy_files(store: Any, bucket: str, out: Path) -> dict[str, int]:
    """Copies the original CVs out of the object store. Keys are content hashes, so a file already
    copied is the same file: it is counted and skipped (BR-107)."""
    directory = out / "files"
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    counts = {"objects": 0, "copied": 0, "already_there": 0, "bytes_copied": 0}
    pages = store.get_paginator("list_objects_v2").paginate(Bucket=bucket)
    for page in pages:
        for entry in page.get("Contents", []):
            key = str(entry["Key"])
            counts["objects"] += 1
            target = directory / key.replace("/", "__")
            if target.exists() and target.stat().st_size == int(entry["Size"]):
                counts["already_there"] += 1
                continue
            body = store.get_object(Bucket=bucket, Key=key)["Body"].read()
            _write_private(target, body)
            counts["copied"] += 1
            counts["bytes_copied"] += len(body)
    return counts


ROLES_SQL = Path(__file__).resolve().parents[2] / "docker" / "postgres" / "roles.sql"
DRILL_IMAGE = "postgres:16-alpine"
DRILL_PREFIX = "talent-drill-"


def _docker(*arguments: str, stdin: bytes | None = None, check_ok: bool = True) -> str:
    # Every argument is built here; nothing comes from a request.
    finished = subprocess.run(["docker", *arguments], input=stdin, capture_output=True, check=False)
    if check_ok and finished.returncode != 0:
        lines = finished.stderr.decode("utf-8", "replace").strip().splitlines()
        raise BackupError(f"docker {arguments[0]} failed: {lines[-1] if lines else 'no output'}")
    return finished.stdout.decode("utf-8", "replace")


def _in_drill(name: str, sql: str, database: str = "talent") -> str:
    return _docker(
        "exec",
        "-i",
        name,
        "psql",
        "-U",
        "talent_owner",
        "-d",
        database,
        "--no-align",
        "--tuples-only",
        "-v",
        "ON_ERROR_STOP=1",
        "-c",
        sql,
    )


def drill(
    dump_path: Path,
    *,
    image: str = DRILL_IMAGE,
    roles_sql: Path = ROLES_SQL,
    keep_container: bool = False,
) -> dict[str, Any]:
    """Restores a dump into a Postgres container that holds nothing but the software (NFR-02).

    The nightly check restores on the same server, where the roles and grants already exist, so a
    dump that lacks them still restores there. Here nothing exists. The only thing created by
    hand is what the runbook says to create: the roles, from docker/postgres/roles.sql. Then the
    rows are counted against the backup's manifest and the application role's permissions are
    checked, because a restore the application cannot read is not a restore.
    """

    manifest_path = dump_path.with_suffix(".json")
    manifest = (
        json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.exists() else {}
    )
    name = DRILL_PREFIX + datetime.now(UTC).strftime("%Y%m%d%H%M%S")
    started = datetime.now(UTC)
    body = dump_path.read_bytes()
    _docker(
        "run",
        "--detach",
        "--name",
        name,
        "--network",
        "none",
        "--env",
        "POSTGRES_USER=talent_owner",
        "--env",
        f"POSTGRES_PASSWORD={secrets.token_urlsafe(24)}",
        "--env",
        "POSTGRES_DB=talent",
        image,
    )
    try:
        # The image starts a temporary server to initialise, stops it, then starts the real one:
        # the second "ready" line in the log is the one to wait for.
        for _ in range(90):
            logs = subprocess.run(["docker", "logs", name], capture_output=True, check=False)
            text_out = (logs.stdout + logs.stderr).decode("utf-8", "replace")
            if text_out.count("ready to accept connections") >= 2:
                break
            time.sleep(1)
        else:
            raise BackupError(f"the drill container {name} did not start in 90 seconds")

        # First, exactly as dumped, into an empty database: whatever is missing shows here.
        _in_drill(name, "CREATE DATABASE bare", database="postgres")
        bare = subprocess.run(
            [
                "docker",
                "exec",
                "-i",
                name,
                "pg_restore",
                "-U",
                "talent_owner",
                "-d",
                "bare",
                "--no-owner",
                "--exit-on-error",
            ],
            input=body,
            capture_output=True,
            check=False,
        )
        bare_error = bare.stderr.decode("utf-8", "replace").strip().splitlines()
        needs_by_hand = [] if bare.returncode == 0 else [bare_error[-1] if bare_error else "?"]

        # Then as the runbook says: create the roles, then restore.
        _docker(
            "exec",
            "-i",
            name,
            "psql",
            "-U",
            "talent_owner",
            "-d",
            "talent",
            "-v",
            "ON_ERROR_STOP=1",
            "-v",
            f"app_password={secrets.token_urlsafe(24)}",
            "-f",
            "-",
            stdin=roles_sql.read_bytes(),
        )
        restore = subprocess.run(
            [
                "docker",
                "exec",
                "-i",
                name,
                "pg_restore",
                "-U",
                "talent_owner",
                "-d",
                "talent",
                "--no-owner",
                "--exit-on-error",
            ],
            input=body,
            capture_output=True,
            check=False,
        )
        if restore.returncode != 0:
            lines = restore.stderr.decode("utf-8", "replace").strip().splitlines()
            raise BackupError(
                "the dump does not restore into an empty machine even with the roles created: "
                + (lines[-1] if lines else f"exit {restore.returncode}")
            )

        schemas = ", ".join(f"'{s}'" for s in COUNTED_SCHEMAS)
        tables = _in_drill(
            name,
            "SELECT format('%I.%I', schemaname, relname) FROM pg_stat_user_tables "
            f"WHERE schemaname IN ({schemas}) ORDER BY 1",
        ).split()
        restored = {t: int(_in_drill(name, f"SELECT count(*) FROM {t}").strip()) for t in tables}
        expected: dict[str, int] = manifest.get("row_counts") or {}
        mismatched = {
            t: {"backup": expected.get(t), "restored": restored.get(t)}
            for t in sorted(set(expected) | set(restored))
            if expected.get(t) != restored.get(t)
        }
        permissions = {
            "app_reads_candidates": "SELECT has_table_privilege('talent_app', "
            "'core.candidate', 'SELECT')",
            "app_writes_captures": "SELECT has_table_privilege('talent_app', "
            "'raw.capture', 'INSERT')",
            "app_cannot_delete_captures": "SELECT NOT has_table_privilege('talent_app', "
            "'raw.capture', 'DELETE')",
            "app_cannot_delete_candidates": "SELECT NOT has_table_privilege('talent_app', "
            "'core.candidate', 'DELETE')",
            "append_only_triggers_present": "SELECT count(*) > 0 FROM pg_trigger "
            "WHERE tgname LIKE '%append_only%'",
        }
        checks = {k: _in_drill(name, q).strip() == "t" for k, q in permissions.items()}
        revision = _in_drill(name, "SELECT version_num FROM alembic_version").strip()
    finally:
        if not keep_container:
            _docker("rm", "--force", "--volumes", name, check_ok=False)

    result = {
        "backup": dump_path.name,
        "container": name,
        "image": image,
        "seconds": round((datetime.now(UTC) - started).total_seconds(), 1),
        "created_by_hand": ["roles: docker/postgres/roles.sql (talent_rw, talent_app)"],
        "restore_without_roles": needs_by_hand[0] if needs_by_hand else "restored",
        "tables": len(restored),
        "rows": sum(restored.values()),
        "row_counts_match": not mismatched,
        "row_count_differences": mismatched,
        "alembic_revision": revision,
        "revision_matches": revision == manifest.get("alembic_revision", revision),
        "permissions": checks,
        "ok": not mismatched and all(checks.values()),
    }
    _write_private(
        dump_path.with_suffix(".drill.json"),
        json.dumps(result, indent=2, sort_keys=True).encode("utf-8"),
    )
    return result


def _chosen(out: Path, backup: str | None) -> Path:
    if backup:
        chosen = Path(backup).expanduser()
        return chosen if chosen.is_absolute() else out / backup
    dumps = sorted(out.glob("talent-*.dump"), reverse=True)
    if not dumps:
        raise BackupError(f"No backup in {out}.")
    return dumps[0]


def _out_directory(value: str | None) -> Path:
    raw = value or os.environ.get("TALENT_BACKUP_DIR") or ""
    if not raw:
        raise BackupError("Set TALENT_BACKUP_DIR or pass --out.")
    out = Path(raw).expanduser()
    check_outside_repository(out, os.environ.get("TALENT_HOST_BACKUP_DIR"), "--out")
    return out


def _dsn() -> str:
    for name in ("TALENT_BACKUP_DSN", "TALENT_DB_MIGRATION_DSN", "TALENT_DB_DSN"):
        value = os.environ.get(name)
        if value:
            return value
    raise BackupError(
        "No database to back up. Set TALENT_BACKUP_DSN (the owner role, so the dump is complete)."
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m ops.backup", description=__doc__.splitlines()[0]
    )
    parser.add_argument("--out", help="where backups are kept; outside any git repository")
    parser.add_argument("--tools", default=os.environ.get("TALENT_BACKUP_TOOLS", "local"))
    commands = parser.add_subparsers(dest="command", required=True)
    take_command = commands.add_parser("take", help="dump, restore it as a check, prune old ones")
    take_command.add_argument("--keep", type=int, default=KEEP_DEFAULT)
    take_command.add_argument(
        "--no-verify", action="store_true", help="skip the restore check (not for scheduled runs)"
    )
    verify = commands.add_parser("verify", help="restore a backup and count what came back")
    verify.add_argument("--backup", help="which one; the newest by default")
    drill_command = commands.add_parser(
        "drill", help="restore a backup into a Postgres container that holds nothing else"
    )
    drill_command.add_argument("--backup", help="which one; the newest by default")
    drill_command.add_argument("--image", default=DRILL_IMAGE)
    drill_command.add_argument("--keep-container", action="store_true")
    commands.add_parser("files", help="copy the original CVs out of the object store")
    commands.add_parser("list", help="what is in the backup directory")
    args = parser.parse_args(argv)

    try:
        out = _out_directory(args.out)
        tools = Tools(args.tools)
        if args.command == "list":
            for row in listed(out):
                mark = "restored" if row["restored"] else "NOT RESTORED"
                size = f"{row['bytes'] / 1e6:8.1f} MB"
                print(f"{row['file']}  {size}  {row['alembic_revision']}  {mark}")
            return 0
        if args.command == "files":
            from importer.blobs import s3_store

            settings_store = s3_store()
            counts = copy_files(settings_store.client, settings_store.bucket, out)
            print(
                f"Files: {counts['objects']} in the store, {counts['copied']} copied, "
                f"{counts['already_there']} already here."
            )
            return 0

        if args.command == "drill":
            chosen = _chosen(out, args.backup)
            result = drill(chosen, image=args.image, keep_container=args.keep_container)
            print(json.dumps(result, indent=2, sort_keys=True))
            return 0 if result["ok"] else 1

        server = Server(_dsn())
        if args.command == "verify":
            dumps = sorted(out.glob("talent-*.dump"), reverse=True)
            if args.backup:
                chosen = Path(args.backup).expanduser()
                if not chosen.is_absolute():
                    chosen = out / args.backup
            elif dumps:
                chosen = dumps[0]
            else:
                raise BackupError(f"No backup in {out}.")
            result = check(server, tools, chosen)
            print(
                f"{chosen.name} restored into {result['restored_into']} in {result['seconds']}s: "
                f"{result['rows']:,} rows across {result['tables']} tables, at "
                f"{result['alembic_revision']}, {result['grants_to_the_app_role']} grant(s) to the "
                "application role."
            )
            return 0

        manifest = take(server, tools, out, keep=args.keep, verify=not args.no_verify)
        restored = manifest["restored"]
        print(
            f"{manifest['file']}  {manifest['bytes'] / 1e6:.1f} MB  "
            f"{sum(manifest['row_counts'].values()):,} rows  at {manifest['alembic_revision']}."
        )
        if restored:
            print(
                f"  restored and counted back in {restored['seconds']}s"
                + (
                    f"; {sum(restored['rows_written_since_the_dump'].values())} rows were written "
                    "while it ran"
                    if restored["rows_written_since_the_dump"]
                    else ""
                )
            )
        else:
            print("  NOT restored: this file has not been proved to work.")
        if manifest["pruned"]:
            print(f"  pruned {len(manifest['pruned'])} old backup(s)")
        return 0
    except (BackupError, DataLocationError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
