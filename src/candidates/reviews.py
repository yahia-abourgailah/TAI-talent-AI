"""Review items about a candidate rather than an application (BR-407, BR-308, BR-202).

  flagged_document      a CV a person must look at: the OCR found hidden content, or reading it
                        failed. The candidate is never told.
  unverified_candidate  a candidate typed in by hand, not yet checked.
  possible_duplicate    two records the matcher believes are one person.
  borderline_score      an evaluation close to a tier line, naming the tiers either side (BR-310).

They share pipeline.review_item with proposed rejections but are served on their own path
(/v1/candidate-review-items), because they carry no application. Scope follows the candidate:
a recruiter sees the items of candidates they can see; a TA lead, an admin and the criteria owner
see all. A person resolves an item as checked, or dismisses it with a reason.
"""

from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Connection

from candidates.reads import VISIBLE
from pipeline.access import Actor, NotFound, Refused, run

KINDS = ("flagged_document", "unverified_candidate", "possible_duplicate", "borderline_score")
REVIEW_ITEM_NOT_FOUND = "Review item not found."

_ITEMS = f"""
    SELECT r.id, r.kind, r.candidate_id, r.capture_id, r.reason_code, r.proposed_by,
           r.proposed_at, x.outcome AS resolution, x.reason AS resolution_reason,
           x.resolved_by, x.resolved_at,
           r.evaluation_id, r.tier_above, r.tier_below,
           r.match_id, m.strength AS match_strength, m.evidence AS match_evidence,
           CASE WHEN m.lower_id = r.candidate_id THEN m.higher_id ELSE m.lower_id END
             AS match_other_candidate_id
    FROM pipeline.review_item r
    JOIN core.candidate c ON c.id = r.candidate_id
    LEFT JOIN pipeline.review_resolution x ON x.review_item_id = r.id
    LEFT JOIN core.candidate_match m ON m.id = r.match_id
    WHERE r.kind IN {tuple(KINDS)!r} AND {VISIBLE}
"""


def get_item(conn: Connection, actor: Actor, review_item_id: int) -> dict[str, Any]:
    rows = run(conn, text(_ITEMS + " AND r.id = :id"), {"id": review_item_id, **actor.scope()})
    if not rows:
        raise NotFound(REVIEW_ITEM_NOT_FOUND)
    return rows[0]


def list_items(
    conn: Connection,
    actor: Actor,
    *,
    limit: int,
    before: int | None = None,
    status: str | None = None,
    kind: str | None = None,
    candidate_id: int | None = None,
) -> list[dict[str, Any]]:
    """Newest first. `status` is open (no resolution yet) or resolved."""
    return run(
        conn,
        text(
            _ITEMS
            + """
              AND (CAST(:before AS bigint) IS NULL OR r.id < :before)
              AND (CAST(:status AS text) IS NULL
                   OR (CAST(:status AS text) = 'open') = (x.id IS NULL))
              AND (CAST(:kind AS text) IS NULL OR r.kind = :kind)
              AND (CAST(:candidate AS bigint) IS NULL OR r.candidate_id = :candidate)
            ORDER BY r.id DESC LIMIT :limit
            """
        ),
        {
            "limit": limit,
            "before": before,
            "status": status,
            "kind": kind,
            "candidate": candidate_id,
            **actor.scope(),
        },
    )


def resolve(
    conn: Connection, actor: Actor, review_item_id: int, outcome: str, reason: str | None
) -> dict[str, Any]:
    """checked (reason optional) or dismissed (reason required). Once per item."""
    item = get_item(conn, actor, review_item_id)
    if item["resolution"] is not None:
        raise Refused("This review item is already resolved.", code="review_item_resolved")
    run(
        conn,
        text(
            "INSERT INTO pipeline.review_resolution (review_item_id, outcome, reason, resolved_by) "
            "VALUES (:item, :outcome, :reason, :by)"
        ),
        {"item": review_item_id, "outcome": outcome, "reason": reason, "by": actor.subject},
    )
    return get_item(conn, actor, review_item_id)
