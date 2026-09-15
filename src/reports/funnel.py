"""The funnel, counted only from recorded moves (A3: BR-601, BR-109).

An application has reached a step when a move went to it. What it did next is the move out of that
step: moved on, or rejected with a reason. Nothing here reads an application's current step.

  reached        applications with a move to the step
  moved on       of those, the ones whose next move went to a step that is not a rejection
  rejected here  of those, the ones whose next move was a rejection, split by reason
  still here     of those, the ones with no move out yet
  conversion     moved on / reached

Steps and their order come from the step list in force, so loading TA's list changes no code; the
rejected step is shown as the split, not as a row. The date range selects arrivals at a step by
moved_at (from inclusive, to exclusive); what happened next is counted up to the end of the range.

Contactability sits next to volume: how many of the candidates counted have a phone or an email in
core.candidate_field_current.
"""

from collections import Counter
from collections.abc import Sequence
from datetime import datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Connection

from pipeline.store import active_step_list
from reports import ReportRefused

GROUPINGS: dict[str, str] = {
    "opening": "CAST(a.opening_id AS text)",
    "brand": "o.brand",
    "recruiter": "a.owner_recruiter",
    "team": "a.team",
}
SOURCE_NOT_OFFERED = (
    "Grouping by source is not offered yet: an application has no channel or tracking code until "
    "that is added to it."
)
CONTACT_FIELDS = ["phone", "email"]


def ratio(part: int, whole: int) -> float | None:
    return None if whole == 0 else round(part / whole, 4)


def contactable(conn: Connection, candidate_ids: Sequence[int]) -> set[int]:
    """The candidates, of those given, with a phone or an email on record."""
    if not candidate_ids:
        return set()
    rows = conn.execute(
        text(
            "SELECT DISTINCT candidate_id FROM core.candidate_field_current "
            "WHERE candidate_id = ANY(:ids) AND field = ANY(:fields) AND value IS NOT NULL"
        ),
        {"ids": list(candidate_ids), "fields": CONTACT_FIELDS},
    ).scalars()
    return {int(candidate_id) for candidate_id in rows}


def contactability_of_all_candidates(conn: Connection) -> dict[str, Any]:
    total, reachable = conn.execute(
        text(
            """
            -- One pass over the phone and email rows: filtering on field reaches inside the
            -- current-value view, where a per-candidate EXISTS would rebuild it for every one.
            SELECT (SELECT count(*) FROM core.candidate),
                   count(DISTINCT candidate_id)
            FROM core.candidate_field_current
            WHERE field = ANY(:fields) AND value IS NOT NULL
            """
        ),
        {"fields": CONTACT_FIELDS},
    ).one()
    return {
        "candidates": total,
        "contactable": reachable,
        "contactability": ratio(reachable, total),
    }


def _check(group_by: str | None, date_from: datetime | None, date_to: datetime | None) -> None:
    if group_by == "source":
        raise ReportRefused(SOURCE_NOT_OFFERED)
    if group_by is not None and group_by not in GROUPINGS:
        raise ReportRefused(f"group_by is one of: {', '.join(GROUPINGS)}.")
    if date_from is not None and date_to is not None and date_from >= date_to:
        raise ReportRefused("from must be before to.")


def funnel_report(
    conn: Connection,
    *,
    group_by: str | None = None,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    opening_ids: Sequence[int] | None = None,
) -> dict[str, Any]:
    _check(group_by, date_from, date_to)
    step_list = active_step_list(conn)
    steps = [step for step in step_list["steps"] if step["outcome"] != "rejected"]
    codes = {step["code"] for step in steps}
    key = GROUPINGS[group_by] if group_by else "'all'"

    moves = conn.execute(
        text(
            f"""
            SELECT m.application_id, m.sequence, m.to_step, m.reason_code, m.moved_at,
                   s.outcome AS to_outcome, a.candidate_id, {key} AS group_key
            FROM pipeline.move m
            JOIN pipeline.application a ON a.id = m.application_id
            JOIN pipeline.opening o ON o.id = a.opening_id
            LEFT JOIN pipeline.step s ON s.list_version = m.list_version AND s.code = m.to_step
            WHERE (CAST(:date_to AS timestamptz) IS NULL OR m.moved_at < :date_to)
              AND (CAST(:openings AS bigint[]) IS NULL OR a.opening_id = ANY(:openings))
            ORDER BY m.application_id, m.sequence
            """
        ),
        {"date_to": date_to, "openings": None if opening_ids is None else list(opening_ids)},
    ).all()
    following = {(move.application_id, move.sequence): move for move in moves}

    groups: dict[str, dict[str, Any]] = {}
    not_in_list = 0
    for move in moves:
        if date_from is not None and move.moved_at < date_from:
            continue
        if move.to_step not in codes:
            not_in_list += move.to_outcome != "rejected"
            continue
        group = groups.setdefault(
            str(move.group_key),
            {
                "applications": set(),
                "candidates": set(),
                "steps": {code: Counter[str]() for code in codes},
            },
        )
        group["applications"].add(move.application_id)
        group["candidates"].add(move.candidate_id)
        counts = group["steps"][move.to_step]
        counts["reached"] += 1
        after = following.get((move.application_id, move.sequence + 1))
        if after is None:
            counts["still_here"] += 1
        elif after.to_outcome == "rejected":
            counts[f"rejected:{after.reason_code}"] += 1
        else:
            counts["moved_on"] += 1

    reachable = contactable(conn, sorted({c for g in groups.values() for c in g["candidates"]}))
    rows = []
    for name in sorted(groups):
        group = groups[name]
        step_rows = []
        for step in steps:
            counts = group["steps"][step["code"]]
            by_reason = {
                label.split(":", 1)[1]: n
                for label, n in sorted(counts.items())
                if label.startswith("rejected:")
            }
            step_rows.append(
                {
                    "step": step["code"],
                    "label": step["label"],
                    "reached": counts["reached"],
                    "moved_on": counts["moved_on"],
                    "rejected_here": sum(by_reason.values()),
                    "rejected_by_reason": by_reason,
                    "still_here": counts["still_here"],
                    "conversion": ratio(counts["moved_on"], counts["reached"]),
                }
            )
        candidates = group["candidates"]
        rows.append(
            {
                "group": name,
                "applications": len(group["applications"]),
                "candidates": len(candidates),
                "contactable_candidates": len(candidates & reachable),
                "contactability": ratio(len(candidates & reachable), len(candidates)),
                "steps": step_rows,
            }
        )

    return {
        "step_list": step_list["version"],
        "provisional": step_list["provisional"],
        "group_by": group_by,
        "from": None if date_from is None else date_from.isoformat(),
        "to": None if date_to is None else date_to.isoformat(),
        "groups": rows,
        "arrivals_at_steps_not_in_the_list": not_in_list,
        "all_candidates": contactability_of_all_candidates(conn),
    }
