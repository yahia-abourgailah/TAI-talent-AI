"""Idempotency-Key on requests that create something (API plan section 5).

The same key with the same request, from the same person, within 24 hours returns the original
response and does nothing again. The same key with a different request is refused with 409. Keys
are per person, so two people never collide, and requests with one key run one at a time.

Only successes are kept: a refused request rolls back with its transaction, so retrying it runs it
again. Kept responses hold ids, codes and timestamps only; a response that could carry candidate
data must not go through here. Rows are append-only: an expired key is not matched, and using it
again adds a row.
"""

import hashlib
import json
import uuid
from collections.abc import Callable

from fastapi import Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.engine import Connection

from api.errors import ApiError

HEADER = "Idempotency-Key"
REPLAYED_HEADER = "Idempotent-Replayed"
KEEP_HOURS = 24
_LOCK_NAMESPACE = 7302


def _parse(key: str) -> uuid.UUID:
    try:
        return uuid.UUID(key)
    except ValueError:
        raise ApiError(
            400,
            "invalid_idempotency_key",
            "Idempotency-Key must be a UUID you generate for each distinct request.",
        ) from None


def respond(
    conn: Connection,
    request: Request,
    subject: str,
    key: str | None,
    body: BaseModel,
    status_code: int,
    make: Callable[[], BaseModel],
) -> JSONResponse:
    """Runs `make` once per key; a repeat of the same request gets the first response back."""
    if key is None:
        return JSONResponse(make().model_dump(mode="json"), status_code=status_code)
    parsed = _parse(key)
    digest = hashlib.sha256(
        f"{request.method} {request.url.path}\n".encode() + body.model_dump_json().encode("utf-8")
    ).digest()

    conn.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:lock, :namespace))"),
        {"lock": f"{subject}\n{parsed}", "namespace": _LOCK_NAMESPACE},
    )
    found = conn.execute(
        text(
            """
            SELECT request_sha256, status_code, response FROM api.idempotency_key
            WHERE subject = :subject AND key = :key
              AND created_at > clock_timestamp() - make_interval(hours => :hours)
            ORDER BY created_at DESC LIMIT 1
            """
        ),
        {"subject": subject, "key": parsed, "hours": KEEP_HOURS},
    ).one_or_none()
    if found is not None:
        if bytes(found.request_sha256) != digest:
            raise ApiError(
                409,
                "idempotency_key_reused",
                "This Idempotency-Key was already used for a different request. "
                "Generate a new key.",
            )
        return JSONResponse(
            found.response, status_code=found.status_code, headers={REPLAYED_HEADER: "true"}
        )

    payload = make().model_dump(mode="json")
    conn.execute(
        text(
            "INSERT INTO api.idempotency_key (subject, key, request_sha256, status_code, response) "
            "VALUES (:subject, :key, :digest, :status, CAST(:response AS jsonb))"
        ),
        {
            "subject": subject,
            "key": parsed,
            "digest": digest,
            "status": status_code,
            "response": json.dumps(payload, ensure_ascii=False),
        },
    )
    return JSONResponse(payload, status_code=status_code)
