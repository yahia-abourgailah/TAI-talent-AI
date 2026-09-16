"""A CV uploaded on the careers page (B1, B2: BR-102, BR-106, BR-107).

    upload  ->  the file kept under its hash, a raw.capture row, a candidate  ->  read_cv queued

The same bytes are the same capture and the same candidate, whatever the file was called: a
second upload only adds an upload row with its own token, pointing at both. Only a new file is
queued for reading, so a CV is read once.

The upload token is shown once and stored only as its SHA-256. Status is read with the upload id
and the token, and says processing, ready (with the form) or failed. It never says whether the OCR
found hidden content (BR-308): a flagged CV is ready like any other, and a person reviews it.
"""

import hashlib
import secrets
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Connection

from importer.blobs import BlobStore
from intake.cv_fields import STORED_FIELDS
from intake.files import FileKind
from intake.reading import JOB_KIND, reading_ref
from intake.records import add_candidate, keep_original
from jobs.queue import enqueue

SOURCE = "cv_upload"
RECEIVED_BY = "careers-page"
TOKEN_HOURS = 24

PROCESSING = "processing"
READY = "ready"
FAILED = "failed"


@dataclass(frozen=True, slots=True)
class Upload:
    upload_id: int
    token: str
    expires_at: datetime
    capture_id: int
    candidate_id: int
    new_file: bool


def token_hash(token: str) -> bytes:
    return hashlib.sha256(token.encode("utf-8")).digest()


def receive_cv(conn: Connection, blobs: BlobStore, content: bytes, kind: FileKind) -> Upload:
    capture = keep_original(
        conn,
        blobs,
        source=SOURCE,
        external_id=None,
        content=content,
        media_type=kind.media_type,
        extension=kind.extension,
        received_by=RECEIVED_BY,
    )
    candidate_id = add_candidate(conn, capture.id, f"{SOURCE}:{capture.sha256.hex()}", RECEIVED_BY)
    if capture.new:
        enqueue(conn, JOB_KIND, {"capture_id": capture.id, "attempt": 1}, RECEIVED_BY)

    token = secrets.token_urlsafe(32)
    row = conn.execute(
        text(
            """
            INSERT INTO intake.cv_upload (capture_id, candidate_id, token_sha256, expires_at)
            VALUES (:capture, :candidate, :token,
                    clock_timestamp() + make_interval(hours => :hours))
            RETURNING id, expires_at
            """
        ),
        {
            "capture": capture.id,
            "candidate": candidate_id,
            "token": token_hash(token),
            "hours": TOKEN_HOURS,
        },
    ).one()
    return Upload(
        upload_id=int(row.id),
        token=token,
        expires_at=row.expires_at,
        capture_id=capture.id,
        candidate_id=candidate_id,
        new_file=capture.new,
    )


def upload_status(conn: Connection, upload_id: int, token: str) -> dict[str, Any] | None:
    """processing, ready or failed, with the form when ready. None for a wrong or expired token."""
    found = conn.execute(
        text(
            """
            SELECT u.id, u.candidate_id, u.expires_at, r.id AS reading_id, r.outcome
            FROM intake.cv_upload u
            LEFT JOIN intake.cv_reading r ON r.capture_id = u.capture_id
            WHERE u.id = :id AND u.token_sha256 = :token AND u.expires_at > clock_timestamp()
            """
        ),
        {"id": upload_id, "token": token_hash(token)},
    ).one_or_none()
    if found is None:
        return None
    status = {None: PROCESSING, "read": READY, "failed": FAILED}[found.outcome]
    result: dict[str, Any] = {
        "upload_id": int(found.id),
        "status": status,
        "expires_at": found.expires_at,
        "fields": None,
    }
    if status == READY:
        rows = conn.execute(
            text(
                """
                SELECT field, value, verification_status, inference, language
                FROM core.candidate_field
                WHERE candidate_id = :candidate AND source = 'cv_extraction'
                  AND source_ref LIKE :ref
                """
            ),
            {"candidate": found.candidate_id, "ref": reading_ref(found.reading_id) + "#%"},
        ).mappings()
        by_name = {row["field"]: row for row in rows}
        result["fields"] = {
            name: _form_field(by_name[name]) for name in STORED_FIELDS if name in by_name
        }
    return result


def _form_field(row: Any) -> dict[str, Any]:
    if row["verification_status"] == "not_recorded":
        return {"value": None, "state": "not_recorded"}
    shown: dict[str, Any] = {"value": row["value"], "inference": row["inference"]}
    if row["language"] is not None:
        shown["language"] = row["language"]
    return shown
