"""Erasing a candidate's data when we may no longer keep it (week 7: CR-03).

    python -m ops.retention policy --file FILE --by NAME [--activate]
    python -m ops.retention due [--as-of 2026-12-31]
    python -m ops.retention erase --by NAME [--limit 100] [--confirm]
    python -m ops.retention erase --candidate 4821 --reason request --by NAME --confirm

This is the only thing in the platform that removes data, and it is deliberately awkward: it runs
as the owner role, it needs a policy Legal has activated, it refuses to do anything without
--confirm, and every run writes what it did.

What goes: the field values, the original CV and the reader's answer to it, and the text inside an
evaluation that quotes the person. What stays: the record itself marked erased, its score and tier,
the steps it moved through, the consent it gave, and the fact of the erasure. Nothing that says who
they were; everything last quarter's numbers were built from (BR-205).

No periods are set until Legal answers OPN-07. Until then `due` and `erase` say so and stop.
"""

import argparse
import json
import os
import sys
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Connection, Engine, create_engine

REASONS = ("retention", "request")
APPLIES_TO = ("no_application", "in_process", "rejected", "hired", "withdrawn")


class RetentionError(Exception):
    """The policy or the erasure was refused. The message says what to do about it."""


def owner_engine() -> Engine:
    """Erasure runs as the owner: the app role has no DELETE grant on anything, on purpose."""
    for name in ("TALENT_ERASURE_DSN", "TALENT_DB_MIGRATION_DSN"):
        dsn = os.environ.get(name)
        if dsn:
            return create_engine(dsn)
    raise RetentionError(
        "Erasure runs as the owner role. Set TALENT_ERASURE_DSN (or TALENT_DB_MIGRATION_DSN)."
    )


def in_force(conn: Connection) -> str | None:
    return conn.execute(text("SELECT core.retention_in_force()")).scalar_one_or_none()


def load_policy(conn: Connection, policy: dict[str, Any], by: str, activate: bool = False) -> str:
    """Records a policy, and activates it only when asked. Policies are never edited."""
    version = str(policy.get("version") or "").strip()
    rules = policy.get("rules") or []
    if not version or not rules:
        raise RetentionError("A policy needs a version and at least one rule.")
    kinds = {str(rule.get("applies_to")) for rule in rules}
    if kinds != set(APPLIES_TO):
        missing = sorted(set(APPLIES_TO) - kinds) or sorted(kinds - set(APPLIES_TO))
        raise RetentionError(f"A policy names every case exactly once; check: {missing}")
    if conn.execute(
        text("SELECT 1 FROM core.retention_policy WHERE version = :v"), {"v": version}
    ).first():
        raise RetentionError(f"Policy {version} is already loaded. A change is a new version.")

    conn.execute(
        text(
            "INSERT INTO core.retention_policy (version, provisional, source, note, loaded_by) "
            "VALUES (:version, :provisional, :source, :note, :by)"
        ),
        {
            "version": version,
            "provisional": bool(policy.get("provisional", True)),
            "source": str(policy.get("source") or "unknown"),
            "note": policy.get("note"),
            "by": by,
        },
    )
    for rule in rules:
        months = rule.get("months")
        conn.execute(
            text(
                "INSERT INTO core.retention_rule (policy_version, applies_to, months, note) "
                "VALUES (:version, :applies_to, :months, :note)"
            ),
            {
                "version": version,
                "applies_to": rule["applies_to"],
                "months": None if months is None else int(months),
                "note": rule.get("note"),
            },
        )
    if activate:
        conn.execute(
            text(
                "INSERT INTO core.retention_activation (policy_version, activated_by, note) "
                "VALUES (:version, :by, :note)"
            ),
            {"version": version, "by": by, "note": policy.get("note")},
        )
    return version


def due(
    conn: Connection, as_of: datetime | None = None, limit: int | None = None
) -> list[dict[str, Any]]:
    """Candidates whose time is up and who have not been erased yet, oldest first. Ids only."""
    if in_force(conn) is None:
        raise RetentionError(
            "No retention policy is in force, so nothing is due. Legal owes us the periods "
            "(OPN-07); load and activate a policy first."
        )
    rows = conn.execute(
        text(
            """
            SELECT candidate_id, applies_to, months, last_activity_at, due_at
            FROM core.candidate_retention
            WHERE NOT erased AND due_at IS NOT NULL AND due_at <= :as_of
            ORDER BY due_at
            """
            + ("LIMIT :limit" if limit else "")
        ),
        {"as_of": as_of or datetime.now(UTC), "limit": limit},
    )
    return [dict(row) for row in rows.mappings()]


def summarise(rows: Sequence[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        counts[row["applies_to"]] = counts.get(row["applies_to"], 0) + 1
    return dict(sorted(counts.items()))


def erase_one(
    conn: Connection,
    candidate_id: int,
    *,
    by: str,
    reason: str = "retention",
    policy: str | None = None,
    due_at: datetime | None = None,
) -> dict[str, Any]:
    """Erases one candidate through the database's own door and returns what it removed."""
    if reason not in REASONS:
        raise RetentionError(f"The reason is one of: {', '.join(REASONS)}.")
    row = conn.execute(
        text("SELECT * FROM core.erase_candidate(:candidate, :why, :policy, :actor, :due)"),
        {
            "candidate": candidate_id,
            "why": reason,
            "policy": policy,
            "actor": by,
            "due": due_at,
        },
    ).one()
    return {
        "candidate_id": candidate_id,
        "fields_erased": int(row.fields_erased),
        "files_erased": int(row.files_erased),
        "readings_erased": int(row.readings_erased),
        "evaluations_cleared": int(row.evaluations_cleared),
        "blob_keys": list(row.blob_keys or []),
    }


def delete_files(keys: Sequence[str]) -> int:
    """Removes the original files from the object store. The database rows already point nowhere."""
    if not keys:
        return 0
    from importer.blobs import s3_store

    store = s3_store()
    removed = 0
    for key in keys:
        store.client.delete_object(Bucket=store.bucket, Key=key)
        removed += 1
    return removed


def run(
    engine: Engine,
    *,
    by: str,
    limit: int | None,
    confirm: bool,
    as_of: datetime | None = None,
    files: bool = True,
) -> dict[str, Any]:
    """Erases everything whose time is up. Without --confirm it only says what it would do."""
    with engine.begin() as conn:
        policy = in_force(conn)
        rows = due(conn, as_of, limit)
        report: dict[str, Any] = {
            "policy_version": policy,
            "due": len(rows),
            "by_case": summarise(rows),
            "erased": 0,
            "fields_erased": 0,
            "files_erased": 0,
            "confirmed": confirm,
        }
        if not confirm:
            return report
        keys: list[str] = []
        for row in rows:
            done = erase_one(
                conn,
                int(row["candidate_id"]),
                by=by,
                reason="retention",
                policy=policy,
                due_at=row["due_at"],
            )
            report["erased"] += 1
            report["fields_erased"] += done["fields_erased"]
            keys += done["blob_keys"]
    report["files_erased"] = delete_files(keys) if files else 0
    return report


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m ops.retention", description=__doc__.splitlines()[0]
    )
    commands = parser.add_subparsers(dest="command", required=True)
    policy = commands.add_parser("policy", help="load a policy, and activate it when Legal says so")
    policy.add_argument("--file", type=Path, required=True)
    policy.add_argument("--by", required=True, help="the person loading it")
    policy.add_argument("--activate", action="store_true", help="put it in force from now on")
    listing = commands.add_parser("due", help="whose time is up, as ids and counts")
    listing.add_argument("--as-of", help="a date, to see what falls due later")
    listing.add_argument("--limit", type=int)
    erase = commands.add_parser("erase", help="erase what is due; says what it would do by default")
    erase.add_argument("--by", required=True)
    erase.add_argument("--limit", type=int, default=500)
    erase.add_argument("--candidate", type=int, help="erase this one, on request")
    erase.add_argument("--reason", choices=REASONS, default="retention")
    erase.add_argument("--confirm", action="store_true", help="actually do it")
    args = parser.parse_args(argv)

    try:
        engine = owner_engine()
        if args.command == "policy":
            body = json.loads(args.file.read_text(encoding="utf-8"))
            with engine.begin() as conn:
                version = load_policy(conn, body, args.by, args.activate)
            where = "in force from now" if args.activate else "loaded, NOT in force"
            print(f"Retention policy {version}: {where}.")
            return 0

        if args.command == "due":
            as_of = datetime.fromisoformat(args.as_of).replace(tzinfo=UTC) if args.as_of else None
            with engine.connect() as conn:
                rows = due(conn, as_of, args.limit)
            print(f"{len(rows)} candidate(s) due: {summarise(rows) or 'none'}")
            for row in rows[:20]:
                when = f"{row['due_at']:%Y-%m-%d}"
                print(f"   cand_{row['candidate_id']}  {row['applies_to']}  due {when}")
            if len(rows) > 20:
                print(f"   … and {len(rows) - 20} more")
            return 0

        if args.candidate is not None:
            if not args.confirm:
                print("error: erasing one candidate needs --confirm", file=sys.stderr)
                return 1
            with engine.begin() as conn:
                done = erase_one(conn, args.candidate, by=args.by, reason=args.reason)
            delete_files(done["blob_keys"])
            print(
                f"cand_{args.candidate} erased ({args.reason}): {done['fields_erased']} field(s), "
                f"{len(done['blob_keys'])} file(s), {done['evaluations_cleared']} evaluation(s) "
                "cleared of quoted text."
            )
            return 0

        report = run(engine, by=args.by, limit=args.limit, confirm=args.confirm)
        if not report["confirmed"]:
            print(
                f"{report['due']} candidate(s) are due under {report['policy_version']}: "
                f"{report['by_case'] or 'none'}. Nothing was erased; add --confirm."
            )
        else:
            print(
                f"Erased {report['erased']} candidate(s) under {report['policy_version']}: "
                f"{report['fields_erased']} field(s), {report['files_erased']} file(s)."
            )
        return 0
    except (RetentionError, json.JSONDecodeError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
