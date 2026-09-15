"""Events to the CRM by webhook, retried until delivered (API plan section 7, BR-410).

    python -m integrations.webhooks deliver [--once] [--event evt_N]
    python -m integrations.webhooks status

Each event is POSTed to TALENT_CRM_WEBHOOK_URL as JSON, one event per request, with:

    X-Talent-Event-Id    evt_...
    X-Talent-Timestamp   seconds since 1970
    X-Talent-Signature   sha256=<hex>: HMAC-SHA256 over "<timestamp>.<raw body>", shared secret

A 2xx within 10 seconds delivers it. Anything else is a failed attempt, tried again after about
1 minute, 5 minutes, 30 minutes, 2 hours, then every 6 hours, until 24 hours after the event.
After that it is parked: not sent again, and still readable from GET /v1/events. Every attempt is
recorded and none is changed. Nothing from a response is kept or logged except its status code.

The app role can only read events, so a worker claims one with a transaction-scoped advisory lock
on its id, not a row lock, and checks it is still due once it holds the lock. Two workers never
send the same event at the same time.
"""

import argparse
import hashlib
import hmac
import json
import logging
import sys
import time
from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from sqlalchemy import text
from sqlalchemy.engine import Connection

from api.ids import decode
from config import get_settings
from integrations.events import envelope
from jobs.queue import engine_from_environment
from pipeline.access import NotFound

log = logging.getLogger("talent.webhooks")

TIMEOUT_SECONDS = 10.0
RETRY_GAPS_SECONDS = (60, 300, 1800, 7200, 21600)
GIVE_UP_AFTER = timedelta(hours=24)
SIGNATURE_TOLERANCE_SECONDS = 300
CANDIDATES_PER_CHECK = 20
_LOCK_NAMESPACE = 7303

DELIVERED = "delivered"
FAILED = "failed"


def signature(secret: str, timestamp: int, body: bytes) -> str:
    message = f"{timestamp}.".encode("ascii") + body
    return "sha256=" + hmac.new(secret.encode("utf-8"), message, hashlib.sha256).hexdigest()


def verify(secret: str, timestamp: str, body: bytes, received: str, now: float) -> bool:
    """What the CRM checks: the signature matches, and the request is under 5 minutes old."""
    try:
        sent = int(timestamp)
    except ValueError:
        return False
    if abs(now - sent) > SIGNATURE_TOLERANCE_SECONDS:
        return False
    return hmac.compare_digest(signature(secret, sent, body), received)


def encode_body(event: Mapping[str, Any]) -> bytes:
    return json.dumps(event, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode()


def lock_key(event_id: int) -> int:
    return (_LOCK_NAMESPACE << 32) | (event_id & 0xFFFFFFFF)


@dataclass(frozen=True, slots=True)
class Attempt:
    event_id: str
    attempt: int
    outcome: str
    status_code: int | None
    failure: str | None


_TRIED = """
    WITH tried AS (
      SELECT event_id, count(*) AS tries, max(attempted_at) AS last_at,
             bool_or(outcome = 'delivered') AS delivered
      FROM integration.delivery_attempt GROUP BY event_id
    )
"""

_DUE = (
    _TRIED
    + """
    SELECT e.id, e.type, e.occurred_at, e.data, coalesce(t.tries, 0) AS tries
    FROM integration.event e
    LEFT JOIN tried t ON t.event_id = e.id
    WHERE NOT coalesce(t.delivered, false)
      AND e.recorded_at > CAST(:now AS timestamptz) - make_interval(secs => :give_up)
      AND (
        t.last_at IS NULL
        OR t.last_at + make_interval(
             secs => (CAST(:gaps AS integer[]))[CAST(LEAST(t.tries, :gap_count) AS integer)]
           ) <= CAST(:now AS timestamptz)
      )
      AND (CAST(:event_id AS bigint) IS NULL OR e.id = :event_id)
    ORDER BY e.id
    LIMIT :limit
"""
)


def _due(conn: Connection, now: datetime, event_id: int | None, limit: int) -> list[dict[str, Any]]:
    rows = conn.execute(
        text(_DUE),
        {
            "now": now,
            "give_up": GIVE_UP_AFTER.total_seconds(),
            "gaps": list(RETRY_GAPS_SECONDS),
            "gap_count": len(RETRY_GAPS_SECONDS),
            "event_id": event_id,
            "limit": limit,
        },
    )
    return [dict(row) for row in rows.mappings()]


def _claim(conn: Connection, now: datetime, event_id: int | None) -> dict[str, Any] | None:
    """The oldest due event no other worker holds, locked until this transaction ends."""
    for candidate in _due(conn, now, event_id, CANDIDATES_PER_CHECK):
        locked = conn.execute(
            text("SELECT pg_try_advisory_xact_lock(:key)"), {"key": lock_key(candidate["id"])}
        ).scalar_one()
        if not locked:
            continue
        # Another worker may have delivered it between the first look and the lock.
        still_due = _due(conn, now, int(candidate["id"]), 1)
        if still_due:
            return still_due[0]
    return None


def deliver_next(
    conn: Connection,
    client: httpx.Client,
    url: str,
    secret: str,
    *,
    now: datetime | None = None,
    event_id: int | None = None,
) -> Attempt | None:
    """Sends the oldest event that is due, and records the attempt. None when nothing is due.

    Runs in the caller's transaction: commit after each call, so each attempt is kept, and the
    event's lock is held only while it is sent.
    """
    row = _claim(conn, now or datetime.now(UTC), event_id)
    if row is None:
        return None

    event = envelope(row)
    body = encode_body(event)
    sent_at = int(time.time())
    headers = {
        "Content-Type": "application/json",
        "X-Talent-Event-Id": event["id"],
        "X-Talent-Timestamp": str(sent_at),
        "X-Talent-Signature": signature(secret, sent_at, body),
    }
    status_code: int | None = None
    failure: str | None = None
    try:
        response = client.post(url, content=body, headers=headers)
        status_code = response.status_code
        if not 200 <= status_code < 300:
            failure = "http_status"
    except httpx.TimeoutException:
        failure = "timeout"
    except httpx.HTTPError:
        failure = "connection"

    outcome = DELIVERED if failure is None else FAILED
    attempt = int(row["tries"]) + 1
    conn.execute(
        text(
            "INSERT INTO integration.delivery_attempt "
            "(event_id, attempt, outcome, status_code, failure) "
            "VALUES (:event, :attempt, :outcome, :status, :failure)"
        ),
        {
            "event": row["id"],
            "attempt": attempt,
            "outcome": outcome,
            "status": status_code,
            "failure": failure,
        },
    )
    log.info(
        "event delivery attempt",
        extra={
            "event_id": event["id"],
            "type": row["type"],
            "attempt": attempt,
            "outcome": outcome,
            "status_code": status_code,
            "failure": failure,
        },
    )
    return Attempt(event["id"], attempt, outcome, status_code, failure)


def delivery_status(conn: Connection, now: datetime | None = None) -> dict[str, int]:
    """How many events are delivered, still being tried, and parked after 24 hours."""
    row = (
        conn.execute(
            text(
                _TRIED
                + """
                SELECT
                  count(*) FILTER (WHERE coalesce(t.delivered, false)) AS delivered,
                  count(*) FILTER (WHERE NOT coalesce(t.delivered, false)
                    AND e.recorded_at > CAST(:now AS timestamptz) - make_interval(secs => :give_up)
                  ) AS pending,
                  count(*) FILTER (WHERE NOT coalesce(t.delivered, false)
                    AND e.recorded_at <= CAST(:now AS timestamptz) - make_interval(secs => :give_up)
                  ) AS parked
                FROM integration.event e LEFT JOIN tried t ON t.event_id = e.id
                """
            ),
            {"now": now or datetime.now(UTC), "give_up": GIVE_UP_AFTER.total_seconds()},
        )
        .mappings()
        .one()
    )
    return {name: int(row[name]) for name in (DELIVERED, "pending", "parked")}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m integrations.webhooks", description=__doc__.splitlines()[0]
    )
    commands = parser.add_subparsers(dest="command", required=True)
    deliver = commands.add_parser("deliver", help="send due events, and keep sending")
    deliver.add_argument("--once", action="store_true", help="send what is due now, then stop")
    deliver.add_argument("--event", help="send only this event, if it is due (evt_...)")
    deliver.add_argument("--poll", type=float, default=5.0, help="seconds between checks")
    commands.add_parser("status", help="delivered, pending and parked counts")
    args = parser.parse_args(argv)

    settings = get_settings()
    engine = engine_from_environment()
    if args.command == "status":
        with engine.connect() as conn:
            print(json.dumps(delivery_status(conn)))
        return 0

    secret = settings.crm_webhook_secret.get_secret_value()
    if not settings.crm_webhook_url:
        print(
            "Event delivery is off: set TALENT_CRM_WEBHOOK_URL and TALENT_CRM_WEBHOOK_SECRET.",
            file=sys.stderr,
        )
        return 2
    try:
        only = decode("event", args.event) if args.event else None
    except NotFound:
        print(f"error: {args.event} is not an event id.", file=sys.stderr)
        return 2

    with httpx.Client(timeout=TIMEOUT_SECONDS, follow_redirects=False) as client:
        while True:
            outcomes: Counter[str] = Counter()
            while True:
                with engine.begin() as conn:
                    attempt = deliver_next(
                        conn, client, settings.crm_webhook_url, secret, event_id=only
                    )
                if attempt is None:
                    break
                outcomes[attempt.outcome] += 1
                if only is not None:
                    break
            if outcomes:
                print(f"Delivered {outcomes[DELIVERED]}, failed {outcomes[FAILED]}.")
            if args.once or only is not None:
                return 0
            time.sleep(args.poll)


if __name__ == "__main__":
    sys.exit(main())
