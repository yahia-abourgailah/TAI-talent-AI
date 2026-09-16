"""Candidates a recruiter types in, and fields a person checks (B4: BR-103, BR-202, BR-201).

A typed-in candidate is kept like any other submission: what was sent, as a raw capture (source
manual_entry), then one field row per form field. Every field is unverified, with the recruiter's
name on it; a field left empty is not recorded. The candidate joins the review queue as
unverified_candidate until a person checks them.

Checking a field adds a new, verified row with who and when. The old row stays. A corrected value
is recorded as the checker's (source recruiter_checked); a confirmed one keeps its source.
"""

import json
import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Connection

from candidates.reads import get_candidate
from importer.blobs import BlobStore
from intake.answer import language_of
from intake.cv_fields import STORED_FIELDS
from intake.records import (
    FieldRow,
    add_candidate,
    add_fields,
    keep_original,
    open_candidate_review,
)
from pipeline.access import Actor, NotFound, Refused, run

SOURCE = "manual_entry"
CORRECTED_SOURCE = "recruiter_checked"
REVIEW_REASON = "manual_entry"

# What a recruiter may type in, in form order: the CV form's fields.
FIELDS: tuple[str, ...] = STORED_FIELDS
NUMBER_FIELDS = frozenset({"age", "years_experience", "graduation_year"})


@dataclass(frozen=True, slots=True)
class Entered:
    candidate_id: int
    review_item_id: int | None


def enter_candidate(
    conn: Connection, blobs: BlobStore, actor: Actor, values: dict[str, str | None]
) -> Entered:
    unknown = sorted(set(values) - set(FIELDS))
    if unknown:
        raise ValueError(f"Unknown fields: {', '.join(unknown)}.")
    given = {name: (value or "").strip() or None for name, value in values.items()}
    if not any(given.values()):
        raise ValueError("Enter at least one field.")

    key = uuid.uuid4().hex
    body = {"entered_by": actor.subject, "fields": {n: given.get(n) for n in FIELDS}}
    content = json.dumps(body, ensure_ascii=False, sort_keys=True).encode("utf-8")
    capture = keep_original(
        conn,
        blobs,
        source=SOURCE,
        external_id=key,
        content=content,
        media_type="application/json",
        extension="json",
        received_by=actor.subject,
    )
    candidate_id = add_candidate(conn, capture.id, f"{SOURCE}:{key}", actor.subject)
    rows = []
    for name in FIELDS:
        value = given.get(name)
        if value is None:
            rows.append(FieldRow(name, None, "not_recorded"))
        else:
            language = None if name in NUMBER_FIELDS else language_of(value)
            rows.append(FieldRow(name, value, "unverified", "stated", language))
    add_fields(
        conn,
        candidate_id,
        rows,
        source=SOURCE,
        source_ref=f"raw.capture:{capture.id}",
        recorded_by=actor.subject,
    )
    item = open_candidate_review(conn, candidate_id, REVIEW_REASON, actor.subject)
    return Entered(candidate_id, item)


def verify_field(
    conn: Connection, actor: Actor, candidate_id: int, field: str, value: str | None
) -> dict[str, Any]:
    """A person checks one field: a new verified row. Returns it, without its value."""
    get_candidate(conn, actor, candidate_id)  # scope: out of scope reads as not found
    rows = run(
        conn,
        text(
            "SELECT id, value, source, verification_status, inference, language "
            "FROM core.candidate_field_current WHERE candidate_id = :c AND field = :f"
        ),
        {"c": candidate_id, "f": field},
    )
    if not rows:
        raise NotFound("The candidate has no such field.")
    current = rows[0]
    corrected = value is not None and value.strip() != (current["value"] or "")
    if not corrected and current["verification_status"] == "not_recorded":
        raise Refused(
            "The field is not recorded. Send the value you checked.", code="field_not_recorded"
        )
    if corrected:
        assert value is not None
        new_value = value.strip()
        source, inference = CORRECTED_SOURCE, "stated"
        language = None if field in NUMBER_FIELDS else language_of(new_value)
    else:
        new_value = current["value"]
        source, inference, language = (
            current["source"],
            current["inference"],
            current["language"],
        )
    (row,) = run(
        conn,
        text(
            """
            INSERT INTO core.candidate_field
              (candidate_id, field, value, source, source_ref, verification_status, verified_at,
               verified_by, inference, language, recorded_by)
            VALUES
              (:c, :f, :value, :source, :ref, 'verified', clock_timestamp(), :by, :inference,
               :language, :by)
            RETURNING field, source, verification_status, verified_at, verified_by
            """
        ),
        {
            "c": candidate_id,
            "f": field,
            "value": new_value,
            "source": source,
            "ref": f"core.candidate_field:{current['id']}",
            "by": actor.subject,
            "inference": inference,
            "language": language,
        },
    )
    row["corrected"] = corrected
    return row


def unchecked_fields(conn: Connection, candidate_id: int) -> int:
    return int(
        conn.execute(
            text(
                "SELECT count(*) FROM core.candidate_field_current "
                "WHERE candidate_id = :c AND verification_status = 'unverified'"
            ),
            {"c": candidate_id},
        ).scalar_one()
    )


def close_checked_candidate(conn: Connection, actor: Actor, candidate_id: int) -> int | None:
    """Once no field is unchecked, the candidate's open unverified_candidate item is resolved as
    checked by the person who checked the last field. Returns the item's id, if one was open."""
    if unchecked_fields(conn, candidate_id):
        return None
    found = conn.execute(
        text(
            """
            SELECT r.id FROM pipeline.review_item r
            WHERE r.kind = 'unverified_candidate' AND r.candidate_id = :c
              AND NOT EXISTS (
                SELECT 1 FROM pipeline.review_resolution x WHERE x.review_item_id = r.id
              )
            """
        ),
        {"c": candidate_id},
    ).scalar_one_or_none()
    if found is None:
        return None
    run(
        conn,
        text(
            "INSERT INTO pipeline.review_resolution (review_item_id, outcome, resolved_by) "
            "VALUES (:item, 'checked', :by)"
        ),
        {"item": found, "by": actor.subject},
    )
    return int(found)
