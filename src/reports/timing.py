"""How long a new application waits to be scored (A4: OBJ-06).

  applied   pipeline.application.created_at
  scored    the first computed evaluation for the candidate, under the opening's criteria version,
            at or after the application arrived
  assigned  the owning recruiter is set when the application is created, so today assigned equals
            applied. The report says so rather than inventing a gap.

Per week of arrival (weeks start Monday, UTC): the median, 90th percentile and slowest time from
applied to scored, in seconds. Every application not scored within 12 hours is listed as late,
including one that has no score at all: a missing score is late, never dropped. Migrated candidates
are left out: they have no applications, and their stored scores have no arrival time.
"""

import math
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Connection

from reports import ReportRefused

LATE_AFTER_SECONDS = 12 * 60 * 60
ASSIGNED_NOTE = (
    "Applications are created with their recruiter already set, so assigned equals applied. "
    "The gap becomes real in week 6, when public applications arrive unassigned."
)
EXCLUDED_NOTE = "Migrated candidates are left out: their stored scores have no arrival time."


def quantile(ordered: Sequence[float], q: float) -> float | None:
    """Linear interpolation between the closest ranks, as in PostgreSQL's percentile_cont."""
    if not ordered:
        return None
    position = (len(ordered) - 1) * q
    lower, upper = math.floor(position), math.ceil(position)
    value = ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)
    return round(value, 3)


def _week(moment: datetime) -> str:
    day = moment.astimezone(UTC).date()
    return (day - timedelta(days=day.weekday())).isoformat()


def timing_report(
    conn: Connection,
    *,
    now: datetime | None = None,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    opening_ids: Sequence[int] | None = None,
) -> dict[str, Any]:
    if date_from is not None and date_to is not None and date_from >= date_to:
        raise ReportRefused("from must be before to.")
    checked_at = now or conn.execute(text("SELECT clock_timestamp()")).scalar_one()
    rows = conn.execute(
        text(
            """
            SELECT a.id AS application_id, a.opening_id, a.created_at AS applied_at,
                   (SELECT min(e.evaluated_at) FROM core.evaluation e
                    WHERE e.candidate_id = a.candidate_id
                      AND e.criteria_version_id = o.criteria_version_id
                      AND e.origin = 'computed' AND e.evaluated_at >= a.created_at) AS scored_at
            FROM pipeline.application a
            JOIN pipeline.opening o ON o.id = a.opening_id
            JOIN core.candidate c ON c.id = a.candidate_id
            JOIN raw.capture r ON r.id = c.capture_id
            WHERE r.source <> 'tai_master'
              AND (CAST(:date_from AS timestamptz) IS NULL OR a.created_at >= :date_from)
              AND (CAST(:date_to AS timestamptz) IS NULL OR a.created_at < :date_to)
              AND (CAST(:openings AS bigint[]) IS NULL OR a.opening_id = ANY(:openings))
            ORDER BY a.created_at, a.id
            """
        ),
        {
            "date_from": date_from,
            "date_to": date_to,
            "openings": None if opening_ids is None else list(opening_ids),
        },
    ).all()

    weeks: dict[str, dict[str, Any]] = {}
    late: list[dict[str, Any]] = []
    waiting_within_limit = 0
    for row in rows:
        week = weeks.setdefault(_week(row.applied_at), {"applications": 0, "seconds": []})
        week["applications"] += 1
        if row.scored_at is None:
            waited = (checked_at - row.applied_at).total_seconds()
            if waited > LATE_AFTER_SECONDS:
                late.append(_late(row, waited, "not scored"))
            else:
                waiting_within_limit += 1
            continue
        seconds = (row.scored_at - row.applied_at).total_seconds()
        week["seconds"].append(seconds)
        if seconds > LATE_AFTER_SECONDS:
            late.append(_late(row, seconds, "scored late"))

    return {
        "checked_at": checked_at.isoformat(),
        "late_after_seconds": LATE_AFTER_SECONDS,
        "weeks": [
            {
                "week_starting": name,
                "applications": week["applications"],
                "scored": len(week["seconds"]),
                "not_scored": week["applications"] - len(week["seconds"]),
                "median_seconds": quantile(sorted(week["seconds"]), 0.5),
                "p90_seconds": quantile(sorted(week["seconds"]), 0.9),
                "slowest_seconds": round(max(week["seconds"]), 3) if week["seconds"] else None,
            }
            for name, week in sorted(weeks.items())
        ],
        "late": late,
        "not_scored_yet_within_the_limit": waiting_within_limit,
        "assigned": {"equals_applied": True, "note": ASSIGNED_NOTE},
        "excluded": EXCLUDED_NOTE,
    }


def _late(row: Any, seconds: float, state: str) -> dict[str, Any]:
    return {
        "application_id": row.application_id,
        "opening_id": row.opening_id,
        "applied_at": row.applied_at.isoformat(),
        "scored_at": None if row.scored_at is None else row.scored_at.isoformat(),
        "seconds": round(seconds, 3),
        "state": state,
    }
