"""Everything waiting for a person, in one list, each line with its reason in words (BR-407).

Items with an application (proposed_rejection) are in scope for the recruiter who owns the
application; items about a candidate are in scope for whoever can see the candidate. A TA lead,
an admin and the criteria owner see all. A locked candidate's items are left out, as everywhere
(week 6).

The summary counts open items per kind and gives the age of the oldest, so an item nobody reads
shows up as a number on the report rather than staying unseen.
"""

from datetime import datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Connection

from candidates.reads import VISIBLE
from pipeline.access import Actor, not_locked, run

KINDS = (
    "proposed_rejection",
    "flagged_document",
    "unverified_candidate",
    "possible_duplicate",
    "borderline_score",
    "ai_assessment",
)

# What a recruiter reads, by kind and reason code. A proposed rejection names the reason from the
# list in force. A code missing here is shown as the kind's general sentence, never as the code.
REASONS: dict[tuple[str, str], str] = {
    ("flagged_document", "hidden_content"): (
        "The CV has hidden text in it. Look at the file before using anything from it."
    ),
    ("flagged_document", "ocr_failed"): "We could not read this CV.",
    ("flagged_document", "ocr_timed_out"): "We could not read this CV: reading it took too long.",
    ("flagged_document", "ocr_unavailable"): (
        "We could not read this CV: the CV reader was not available."
    ),
    ("flagged_document", "ocr_rejected"): "We could not read this CV: the CV reader refused it.",
    ("flagged_document", "ocr_answer_unreadable"): (
        "We could not read this CV: the CV reader's answer made no sense."
    ),
    ("flagged_document", "document_missing"): "The CV file is missing.",
    ("unverified_candidate", "manual_entry"): (
        "Typed in by hand and not yet checked. Check the details before anyone contacts them."
    ),
    ("possible_duplicate", "possible_duplicate"): (
        "This may be the same person as another record. Decide whether to join them."
    ),
}
KIND_TEXT: dict[str, str] = {
    "proposed_rejection": "The scoring proposes a rejection. A person confirms or dismisses it.",
    "flagged_document": "The CV needs a person to look at it.",
    "unverified_candidate": "The candidate's details are not checked yet.",
    "possible_duplicate": "This may be the same person as another record.",
    "borderline_score": "The score is close to a tier line.",
    "ai_assessment": (
        "A CV was matched against the skills this job asks for. Read it and decide; nothing was "
        "decided for you."
    ),
}


def reason_text(item: dict[str, Any]) -> str:
    kind, code = item["kind"], item["reason_code"]
    if kind == "proposed_rejection":
        label = item.get("rejection_label")
        return f"The scoring proposes a rejection: {label}." if label else KIND_TEXT[kind]
    if kind == "ai_assessment":
        score = item.get("assessed_score")
        if score is None:
            return (
                "A CV needs matching against the skills this job asks for: nothing usable came "
                "back. Read it yourself."
            )
        return (
            f"A CV was matched against the skills this job asks for and came out at "
            f"{round(float(score))} out of 100. Read it and decide; nothing was decided for you."
        )
    if kind == "borderline_score":
        return (
            f"The score is close to a tier line, between {item['tier_above']} and "
            f"{item['tier_below']}. It keeps the tier it has; decide whether it should."
        )
    return REASONS.get((kind, code), KIND_TEXT.get(kind, "Needs a person to look at it."))


_OPEN = f"""
    SELECT r.id, r.kind, r.reason_code, c.id AS candidate_id,
           coalesce(r.application_id, e.application_id) AS application_id, r.evaluation_id,
           r.tier_above, r.tier_below, e.score AS assessed_score,
           r.proposed_by, r.proposed_at, rr.label AS rejection_label
    FROM pipeline.review_item r
    LEFT JOIN pipeline.application a ON a.id = r.application_id
    -- items written before migration 0010 carry their candidate only through the application
    JOIN core.candidate c ON c.id = coalesce(r.candidate_id, a.candidate_id)
    LEFT JOIN core.evaluation e ON e.id = r.evaluation_id
    LEFT JOIN pipeline.rejection_reason rr
      ON rr.list_version = r.list_version AND rr.code = r.reason_code
    WHERE NOT EXISTS (SELECT 1 FROM pipeline.review_resolution x WHERE x.review_item_id = r.id)
      -- An archived record is out of play (BR-205). Asking somebody to work an item about one is
      -- asking for work that cannot lead anywhere.
      AND c.archived_at IS NULL
      AND {not_locked("c.id")}
      AND CASE WHEN r.application_id IS NOT NULL
               THEN (:sees_all OR a.owner_recruiter = :subject)
               ELSE {VISIBLE}
          END
"""


def list_open(
    conn: Connection,
    actor: Actor,
    *,
    limit: int,
    after: int | None = None,
    kind: str | None = None,
) -> list[dict[str, Any]]:
    """Open items in scope, oldest first: the order a person works through them. The next page
    starts after the last id seen."""
    rows = run(
        conn,
        text(
            _OPEN
            + """
              AND (CAST(:after AS bigint) IS NULL OR r.id > :after)
              AND (CAST(:kind AS text) IS NULL OR r.kind = :kind)
            ORDER BY r.id LIMIT :limit
            """
        ),
        {"limit": limit, "after": after, "kind": kind, **actor.scope()},
    )
    for row in rows:
        row["reason"] = reason_text(row)
    return rows


def summary(conn: Connection, actor: Actor, now: datetime | None = None) -> dict[str, Any]:
    """Open items per kind, with the oldest one's waiting time in hours. Counts only."""
    rows = run(
        conn,
        text(
            f"""
            SELECT q.kind, count(*) AS open,
                   min(q.proposed_at) AS oldest_since,
                   extract(epoch FROM coalesce(CAST(:now AS timestamptz), clock_timestamp())
                                      - min(q.proposed_at)) / 3600.0 AS oldest_hours
            FROM ({_OPEN}) q
            GROUP BY q.kind
            """
        ),
        {"now": now, **actor.scope()},
    )
    found = {row["kind"]: row for row in rows}
    kinds = []
    for kind in KINDS:
        row = found.get(kind)
        kinds.append(
            {
                "kind": kind,
                "open": 0 if row is None else int(row["open"]),
                "oldest_since": None if row is None else row["oldest_since"],
                "oldest_waiting_hours": (
                    None if row is None else round(float(row["oldest_hours"]), 1)
                ),
            }
        )
    oldest_kind, oldest_hours = None, None
    for row in rows:
        hours = round(float(row["oldest_hours"]), 1)
        if oldest_hours is None or hours > oldest_hours:
            oldest_kind, oldest_hours = str(row["kind"]), hours
    return {
        "open": sum(int(row["open"]) for row in rows),
        "oldest_waiting_hours": oldest_hours,
        "oldest_kind": oldest_kind,
        "kinds": kinds,
    }
