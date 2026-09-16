"""Writes shared by uploads, readings and manual entry: raw captures, fields and review items.

Everything written here is append-only. A capture with the same source, external id and content
is the same capture (BR-106); a review item already open for the same thing is not opened twice.
"""

import hashlib
from collections.abc import Iterable
from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.engine import Connection

from importer.blobs import BlobStore

FLAGGED_DOCUMENT = "flagged_document"
UNVERIFIED_CANDIDATE = "unverified_candidate"


@dataclass(frozen=True, slots=True)
class Capture:
    id: int
    new: bool
    sha256: bytes


@dataclass(frozen=True, slots=True)
class FieldRow:
    field: str
    value: str | None
    status: str
    inference: str | None = None
    language: str | None = None


def keep_original(
    conn: Connection,
    blobs: BlobStore,
    *,
    source: str,
    external_id: str | None,
    content: bytes,
    media_type: str,
    extension: str,
    received_by: str,
) -> Capture:
    """Stores the bytes under their hash and records them in raw.capture, once (BR-107)."""
    sha = hashlib.sha256(content).digest()
    blob_key = f"{source}/{sha.hex()}.{extension}"
    blobs.put_if_absent(blob_key, content, media_type)
    params = {"source": source, "external_id": external_id, "sha": sha}
    new_id = conn.execute(
        text(
            """
            INSERT INTO raw.capture
              (source, external_id, content_sha256, blob_key, media_type, byte_size, received_by)
            VALUES (:source, :external_id, :sha, :blob_key, :media_type, :size, :by)
            ON CONFLICT DO NOTHING
            RETURNING id
            """
        ),
        {
            **params,
            "blob_key": blob_key,
            "media_type": media_type,
            "size": len(content),
            "by": received_by,
        },
    ).scalar_one_or_none()
    if new_id is not None:
        return Capture(int(new_id), True, sha)
    found = conn.execute(
        text(
            "SELECT id FROM raw.capture WHERE source = :source "
            "AND coalesce(external_id, '') = coalesce(CAST(:external_id AS text), '') "
            "AND content_sha256 = :sha"
        ),
        params,
    ).scalar_one()
    return Capture(int(found), False, sha)


def add_candidate(conn: Connection, capture_id: int, source_key: str, created_by: str) -> int:
    """The candidate made from a capture. The same source key is the same candidate (BR-106)."""
    created = conn.execute(
        text(
            "INSERT INTO core.candidate (capture_id, source_key, created_by, pipeline_state) "
            "VALUES (:capture, :key, :by, 'not_recorded') "
            "ON CONFLICT (source_key) DO NOTHING RETURNING id"
        ),
        {"capture": capture_id, "key": source_key, "by": created_by},
    ).scalar_one_or_none()
    if created is not None:
        return int(created)
    return int(
        conn.execute(
            text("SELECT id FROM core.candidate WHERE source_key = :key"), {"key": source_key}
        ).scalar_one()
    )


def add_fields(
    conn: Connection,
    candidate_id: int,
    rows: Iterable[FieldRow],
    *,
    source: str,
    source_ref: str,
    recorded_by: str,
) -> int:
    """New field rows, never overwrites. source_ref gets '#<field>' appended. Returns the count."""
    written = 0
    for row in rows:
        conn.execute(
            text(
                """
                INSERT INTO core.candidate_field
                  (candidate_id, field, value, source, source_ref, verification_status,
                   inference, language, recorded_by)
                VALUES
                  (:candidate, :field, :value, :source, :ref, :status, :inference, :language, :by)
                """
            ),
            {
                "candidate": candidate_id,
                "field": row.field,
                "value": row.value,
                "source": source,
                "ref": f"{source_ref}#{row.field}",
                "status": row.status,
                "inference": row.inference,
                "language": row.language,
                "by": recorded_by,
            },
        )
        written += 1
    return written


def open_document_review(
    conn: Connection, candidate_id: int, capture_id: int, reason: str, proposed_by: str
) -> int | None:
    """A CV for a person to look at. None when the same item is already open."""
    found = conn.execute(
        text(
            """
            INSERT INTO pipeline.review_item
              (kind, candidate_id, capture_id, reason_code, proposed_by)
            VALUES ('flagged_document', :candidate, :capture, :reason, :by)
            ON CONFLICT (capture_id, reason_code) WHERE kind = 'flagged_document' DO NOTHING
            RETURNING id
            """
        ),
        {"candidate": candidate_id, "capture": capture_id, "reason": reason, "by": proposed_by},
    ).scalar_one_or_none()
    return None if found is None else int(found)


def open_candidate_review(
    conn: Connection, candidate_id: int, reason: str, proposed_by: str
) -> int | None:
    """A candidate for a person to check. None when one is already open for them."""
    found = conn.execute(
        text(
            """
            INSERT INTO pipeline.review_item (kind, candidate_id, reason_code, proposed_by)
            VALUES ('unverified_candidate', :candidate, :reason, :by)
            ON CONFLICT (candidate_id) WHERE kind = 'unverified_candidate' DO NOTHING
            RETURNING id
            """
        ),
        {"candidate": candidate_id, "reason": reason, "by": proposed_by},
    ).scalar_one_or_none()
    return None if found is None else int(found)
