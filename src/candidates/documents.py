"""A candidate's files, for the people who work with them (B1: BR-107, NFR-09).

A recruiter reaches the files of the candidates they can see (candidates.reads). Every listing and
every download is recorded in audit.document_access with the person's name, so who saw a CV can
always be answered. The file comes back byte for byte as it was uploaded.
"""

from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Connection

from candidates.reads import VISIBLE, get_candidate
from pipeline.access import Actor, NotFound, run

DOCUMENT_NOT_FOUND = "Document not found."

_LOG = text(
    """
    INSERT INTO audit.document_access (capture_id, candidate_id, action, accessed_by, request_id)
    VALUES (:capture, :candidate, :action, :by, :request_id)
    """
)


def _log(
    conn: Connection,
    actor: Actor,
    candidate_id: int,
    action: str,
    request_id: str,
    capture_id: int | None = None,
) -> None:
    conn.execute(
        _LOG,
        {
            "capture": capture_id,
            "candidate": candidate_id,
            "action": action,
            "by": actor.subject,
            "request_id": request_id[:64] or None,
        },
    )


def list_documents(
    conn: Connection, actor: Actor, candidate_id: int, request_id: str = ""
) -> list[dict[str, Any]]:
    """The candidate's CVs, oldest first, with how reading each one ended."""
    get_candidate(conn, actor, candidate_id)
    rows = run(
        conn,
        text(
            """
            SELECT r.id, r.media_type, r.byte_size, r.received_at,
                   x.outcome AS reading, x.failure AS reading_failure,
                   coalesce(x.hidden_content, false) AS hidden_content, x.read_at
            FROM raw.capture r
            -- A file may hold a reading from each reader that has seen it; the newest is the one
            -- in force (migration 0016).
            LEFT JOIN LATERAL (
              SELECT y.outcome, y.failure, y.hidden_content, y.read_at
              FROM intake.cv_reading y WHERE y.capture_id = r.id ORDER BY y.id DESC LIMIT 1
            ) x ON true
            WHERE r.id IN (SELECT capture_id FROM intake.cv_upload WHERE candidate_id = :c)
            ORDER BY r.id
            """
        ),
        {"c": candidate_id},
    )
    _log(conn, actor, candidate_id, "listed", request_id)
    return rows


def open_document(
    conn: Connection, actor: Actor, capture_id: int, request_id: str = ""
) -> dict[str, Any]:
    """Where the file is kept and what it is. Out of scope reads as not found."""
    rows = run(
        conn,
        text(
            f"""
            SELECT r.id, r.blob_key, r.media_type, r.byte_size, u.candidate_id
            FROM raw.capture r
            JOIN LATERAL (
              SELECT candidate_id FROM intake.cv_upload
              WHERE capture_id = r.id ORDER BY id LIMIT 1
            ) u ON true
            JOIN core.candidate c ON c.id = u.candidate_id
            WHERE r.id = :id AND {VISIBLE}
            """
        ),
        {"id": capture_id, **actor.scope()},
    )
    if not rows:
        raise NotFound(DOCUMENT_NOT_FOUND)
    document = rows[0]
    _log(conn, actor, int(document["candidate_id"]), "downloaded", request_id, capture_id)
    return document
