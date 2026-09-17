"""Watching for trouble, and saying so out loud (week 7: NFR-05).

    python -m ops.watch                     # a table, and an exit code
    python -m ops.watch --json              # the same, for a monitoring agent
    python -m ops.watch --alert             # also post to TALENT_ALERT_WEBHOOK_URL when not ok

Exit code 0 everything is fine, 1 something wants looking at today, 2 something is wrong now. A
monitoring agent, a cron line or a systemd OnFailure= can all read that.

Each check answers one question an on-call person would ask, with the number behind it, and every
one names what to do in docs/ops/RUNBOOK.md. Nothing here reads a candidate's name, number or CV:
counts, ages and ids only, because an alert ends up in a chat channel.
"""

import argparse
import json
import os
import sys
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Connection

from jobs.queue import engine_from_environment

OK, WARN, CRITICAL = "ok", "warning", "critical"
# The test suite runs against the development database and names its job kinds test-…; they are
# left out here so a developer's watch shows the platform, not the test run. No kind in staging or
# production is named this way.
NOT_A_REAL_KIND = "test-%"
RANK = {OK: 0, WARN: 1, CRITICAL: 2}
EXIT_CODE = {OK: 0, WARN: 1, CRITICAL: 2}


def _minutes(value: float | None) -> float:
    return round(float(value or 0) / 60, 1)


@dataclass(frozen=True, slots=True)
class Check:
    name: str
    status: str
    detail: str
    numbers: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "check": self.name,
            "status": self.status,
            "detail": self.detail,
            **({"numbers": self.numbers} if self.numbers else {}),
        }


def _level(value: float, warn: float, critical: float) -> str:
    if value >= critical:
        return CRITICAL
    return WARN if value >= warn else OK


def queue_waiting(conn: Connection, *, warn: int = 50, critical: int = 500) -> Check:
    """Work waiting to be done. A queue that only grows means no worker is running."""
    row = conn.execute(
        text(
            """
            SELECT count(*) AS waiting,
                   coalesce(max(EXTRACT(EPOCH FROM (now() - requested_at))), 0) AS oldest_seconds
            FROM jobs.job
            WHERE status = 'queued' AND (run_after IS NULL OR run_after <= now())
              AND kind NOT LIKE :not_real
            """
        ),
        {"not_real": NOT_A_REAL_KIND},
    ).one()
    waiting, oldest = int(row.waiting), _minutes(row.oldest_seconds)
    status = max(
        _level(waiting, warn, critical), _level(oldest, 15, 60), key=lambda name: RANK[name]
    )
    return Check(
        "queue_waiting",
        status,
        f"{waiting} job(s) waiting, the oldest for {oldest} minutes",
        {"waiting": waiting, "oldest_minutes": oldest},
    )


def jobs_failing(conn: Connection, *, hours: int = 24) -> Check:
    """Jobs that gave up in the last day. One is worth a look; a handful is an incident."""
    rows = conn.execute(
        text(
            """
            SELECT kind, count(*) AS failures
            FROM jobs.job
            WHERE status = 'failed' AND finished_at > now() - make_interval(hours => :hours)
              AND kind NOT LIKE :not_real
            GROUP BY kind ORDER BY failures DESC
            """
        ),
        {"hours": hours, "not_real": NOT_A_REAL_KIND},
    ).all()
    total = sum(int(row.failures) for row in rows)
    by_kind = {row.kind: int(row.failures) for row in rows}
    status = _level(total, 1, 5)
    detail = f"{total} job(s) failed in {hours}h" + (f": {by_kind}" if by_kind else "")
    return Check("jobs_failing", status, detail, {"failures": total, "by_kind": by_kind})


def jobs_stuck(conn: Connection, *, minutes: int = 30) -> Check:
    """A job marked running long after any worker could still be on it: the worker died."""
    row = conn.execute(
        text(
            """
            SELECT count(*) AS stuck,
                   coalesce(max(EXTRACT(EPOCH FROM (now() - started_at))), 0) AS oldest_seconds
            FROM jobs.job
            WHERE status = 'running'
              AND started_at < now() - make_interval(mins => :minutes)
              AND kind NOT LIKE :not_real
            """
        ),
        {"minutes": minutes, "not_real": NOT_A_REAL_KIND},
    ).one()
    stuck = int(row.stuck)
    return Check(
        "jobs_stuck",
        CRITICAL if stuck else OK,
        f"{stuck} job(s) have been running for over {minutes} minutes"
        + (f", the oldest {_minutes(row.oldest_seconds)}" if stuck else ""),
        {"stuck": stuck, "oldest_minutes": _minutes(row.oldest_seconds)},
    )


def events_undelivered(conn: Connection, *, warn: int = 20, critical: int = 200) -> Check:
    """Events the CRM has not taken. Delivery is off while no webhook URL is set, and then this is
    simply the size of the catch-up feed."""
    row = conn.execute(
        text(
            """
            SELECT count(*) AS waiting,
                   coalesce(max(EXTRACT(EPOCH FROM (now() - e.recorded_at))), 0) AS oldest_seconds,
                   count(*) FILTER (
                     WHERE (SELECT count(*) FROM integration.delivery_attempt a
                            WHERE a.event_id = e.id) >= 8
                   ) AS parked
            FROM integration.event e
            WHERE NOT EXISTS (
              SELECT 1 FROM integration.delivery_attempt d
              WHERE d.event_id = e.id AND d.outcome = 'delivered'
            )
            """
        )
    ).one()
    waiting, parked = int(row.waiting), int(row.parked)
    status = max(
        _level(waiting, warn, critical), CRITICAL if parked else OK, key=lambda name: RANK[name]
    )
    return Check(
        "events_undelivered",
        status,
        f"{waiting} event(s) not taken by the CRM, {parked} parked after repeated failures",
        {
            "waiting": waiting,
            "parked": parked,
            "oldest_minutes": _minutes(row.oldest_seconds),
        },
    )


def backup_freshness(
    directory: Path | None, *, warn_hours: int = 36, critical_hours: int = 72
) -> Check:
    """The newest backup, and whether it was restored. A backup that stopped is silent otherwise."""
    if directory is None:
        return Check("backup", WARN, "TALENT_BACKUP_DIR is not set, so backups are not watched")
    dumps = sorted(directory.glob("talent-*.dump"), reverse=True)
    if not dumps:
        return Check("backup", CRITICAL, f"no backup in {directory}", {"backups": 0})
    newest = dumps[0]
    age_hours = round((datetime.now(UTC).timestamp() - newest.stat().st_mtime) / 3600, 1)
    manifest_path = newest.with_suffix(".json")
    restored = False
    if manifest_path.exists():
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            restored = bool((manifest.get("restored") or {}).get("ok"))
        except json.JSONDecodeError:
            restored = False
    status = _level(age_hours, warn_hours, critical_hours)
    if not restored:
        status = CRITICAL
    return Check(
        "backup",
        status,
        f"newest backup {newest.name} is {age_hours}h old and was "
        + ("restored as a check" if restored else "NOT restored: it is not proved to work"),
        {"age_hours": age_hours, "restored": restored, "backups": len(dumps)},
    )


def run_checks(conn: Connection, backup_directory: Path | None = None) -> list[Check]:
    return [
        queue_waiting(conn),
        jobs_failing(conn),
        jobs_stuck(conn),
        events_undelivered(conn),
        backup_freshness(backup_directory),
    ]


def overall(checks: Sequence[Check]) -> str:
    return max((check.status for check in checks), key=lambda name: RANK[name], default=OK)


def report(checks: Sequence[Check]) -> dict[str, Any]:
    return {
        "status": overall(checks),
        "checked_at": datetime.now(UTC).isoformat(),
        "checks": [check.as_dict() for check in checks],
        "runbook": "docs/ops/RUNBOOK.md",
    }


def alert_text(checks: Sequence[Check], where: str) -> str:
    """What lands in the chat channel: the failing checks and their numbers, never a person."""
    bad = [check for check in checks if check.status != OK]
    lines = [f"Talent Platform {overall(checks).upper()} on {where}"]
    lines += [f"  [{check.status}] {check.name}: {check.detail}" for check in bad]
    lines.append("  What to do: docs/ops/RUNBOOK.md")
    return "\n".join(lines)


def post_alert(url: str, message: str, timeout: float = 10.0) -> bool:
    import httpx

    try:
        response = httpx.post(url, json={"text": message}, timeout=timeout)
        return response.status_code < 400
    except httpx.HTTPError:
        return False


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m ops.watch", description=__doc__.splitlines()[0]
    )
    parser.add_argument("--json", action="store_true", help="machine-readable, for an agent")
    parser.add_argument(
        "--alert", action="store_true", help="post to TALENT_ALERT_WEBHOOK_URL when not ok"
    )
    parser.add_argument("--backup-dir", help="default: TALENT_BACKUP_DIR")
    args = parser.parse_args(argv)

    raw = args.backup_dir or os.environ.get("TALENT_BACKUP_DIR") or ""
    directory = Path(raw).expanduser() if raw else None
    with engine_from_environment().connect() as conn:
        checks = run_checks(conn, directory)

    if args.json:
        print(json.dumps(report(checks), indent=2))
    else:
        width = max(len(check.name) for check in checks)
        for check in checks:
            mark = {OK: "ok  ", WARN: "WARN", CRITICAL: "CRIT"}[check.status]
            print(f"{mark}  {check.name.ljust(width)}  {check.detail}")
        print(f"\n{overall(checks)}. What to do: docs/ops/RUNBOOK.md")

    if args.alert and overall(checks) != OK:
        url = os.environ.get("TALENT_ALERT_WEBHOOK_URL", "")
        if not url:
            print("error: --alert needs TALENT_ALERT_WEBHOOK_URL", file=sys.stderr)
        elif not post_alert(url, alert_text(checks, os.environ.get("TALENT_ENV", "unknown"))):
            print("error: the alert could not be posted", file=sys.stderr)
            return EXIT_CODE[CRITICAL]
    return EXIT_CODE[overall(checks)]


if __name__ == "__main__":
    sys.exit(main())
