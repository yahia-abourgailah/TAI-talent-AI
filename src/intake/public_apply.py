"""A candidate applying from a job post (B: BR-101, BR-109, BR-602, CR-02; A1 scoring follows).

    job post link -> careers page -> (optional CV upload, week 5) -> apply

One apply does all of this in one transaction:

  1. The requisition must be open and public, or there is nothing to apply to.
  2. A phone or WhatsApp number is required (BR-109). Without one nobody can be reached, so the
     application is refused rather than kept and forgotten.
  3. The candidate is the one the CV upload made, when an upload token is sent; otherwise a new
     candidate is made from the submitted form, kept as its own raw capture.
  4. What the candidate confirmed on the form is recorded as candidate_confirmed field rows, over
     whatever the CV said. Nothing is overwritten: each is a new row (BR-201).
  5. Their consent is recorded with the wording version they were shown (CR-02).
  6. The application is created, owned by the requisition's recruiter. Creating it starts the
     pipeline and queues the scoring (migrations 0005 and 0008), so nothing here scores anyone.

The answer carries an application id and nothing else: no score, no tier, no gate result (BR-302).
"""

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Connection

from candidates.withdrawal import is_locked
from importer.blobs import BlobStore
from intake import consent as consent_records
from intake import job_posts
from intake.answer import language_of
from intake.cv_fields import STORED_FIELDS
from intake.records import FieldRow, add_candidate, add_fields, keep_original
from intake.uploads import token_hash
from pipeline.access import NotFound, Refused

SOURCE = "public_apply"
RECEIVED_BY = "careers-page"
CONTACT_TYPES = ("phone", "whatsapp")
# Refusals the sender can fix: the API answers these 400, not 409.
BAD_REQUEST_CODES = frozenset(
    {"contact_channel_required", "consent_required", "upload_token_required", "invalid_request"}
)
FIELDS: tuple[str, ...] = STORED_FIELDS
NUMBER_FIELDS = frozenset({"age", "years_experience", "graduation_year"})


@dataclass(frozen=True, slots=True)
class Consent:
    agreed: bool
    wording_version: str
    purposes: tuple[str, ...]
    channels: tuple[str, ...]
    language: str
    agreed_at: datetime


@dataclass(frozen=True, slots=True)
class Application:
    requisition_id: int
    fields: dict[str, str | None]
    contact_type: str
    contact_value: str
    consent: Consent
    upload_id: int | None = None
    upload_token: str | None = None
    tracking_code: str | None = None


@dataclass(frozen=True, slots=True)
class Received:
    application_id: int
    candidate_id: int
    consent_id: int


_OPENING = text(
    """
    SELECT id, owner_recruiter, team, status, public, title, location, brand, track, department
    FROM pipeline.opening WHERE id = :id
    """
)
_UPLOAD = text(
    """
    SELECT u.id, u.candidate_id
    FROM intake.cv_upload u
    WHERE u.id = :id AND u.token_sha256 = :token AND u.expires_at > clock_timestamp()
    """
)
_APPLICATION = text(
    """
    INSERT INTO pipeline.application
      (opening_id, candidate_id, owner_recruiter, team, created_by)
    VALUES (:opening, :candidate, :owner, :team, :by)
    RETURNING id
    """
)


def open_requisition(conn: Connection, requisition_id: int) -> dict[str, Any]:
    """A requisition a candidate may apply to. Anything else reads as not found."""
    row = conn.execute(_OPENING, {"id": requisition_id}).mappings().one_or_none()
    if row is None or not row["public"] or row["status"] != "open":
        raise NotFound("Job not found.")
    return dict(row)


def list_open_requisitions(
    conn: Connection, limit: int, before: int | None
) -> list[dict[str, Any]]:
    """Public, open jobs, newest first: what a careers page may show."""
    rows = conn.execute(
        text(
            """
            SELECT id, title, brand, location, track, department, created_at
            FROM pipeline.opening
            WHERE public AND status = 'open'
              AND (CAST(:before AS bigint) IS NULL OR id < :before)
            ORDER BY id DESC LIMIT :limit
            """
        ),
        {"limit": limit, "before": before},
    )
    return [dict(row) for row in rows.mappings()]


def _candidate_from_upload(conn: Connection, application: Application) -> int | None:
    if application.upload_id is None:
        return None
    if not application.upload_token:
        raise Refused("Send the upload token with the upload id.", code="upload_token_required")
    found = conn.execute(
        _UPLOAD,
        {"id": application.upload_id, "token": token_hash(application.upload_token)},
    ).one_or_none()
    if found is None:
        raise NotFound("Upload not found.")
    candidate_id = int(found.candidate_id)
    if is_locked(conn, candidate_id):
        # The record was locked after the candidate asked us to stop keeping their data (BR-504).
        # They are applying again and agreeing again, so this application starts a new record: the
        # locked one stays locked, and a person decides later whether the two are one (BR-206).
        return None
    return candidate_id


def _confirmed_fields(application: Application) -> list[FieldRow]:
    rows = []
    for name in FIELDS:
        value = (application.fields.get(name) or "").strip() or None
        if name == application.contact_type:
            value = application.contact_value
        if value is None:
            continue
        language = None if name in NUMBER_FIELDS else language_of(value)
        rows.append(FieldRow(name, value, "unverified", "stated", language))
    return rows


def apply(conn: Connection, blobs: BlobStore, application: Application) -> Received:
    """Records one application from the careers page, with its consent."""
    if application.contact_type not in CONTACT_TYPES:
        raise Refused(
            "A phone or WhatsApp number is required to apply.", code="contact_channel_required"
        )
    if not application.contact_value.strip():
        raise Refused(
            "A phone or WhatsApp number is required to apply.", code="contact_channel_required"
        )
    if not application.consent.agreed:
        raise Refused("The candidate must agree before applying.", code="consent_required")

    opening = open_requisition(conn, application.requisition_id)
    post = (
        job_posts.resolve(conn, application.tracking_code)
        if application.tracking_code is not None
        else None
    )
    unknown = sorted(set(application.fields) - set(FIELDS))
    if unknown:
        raise Refused(f"Unknown fields: {', '.join(unknown)}.", code="invalid_request")

    candidate_id = _candidate_from_upload(conn, application)
    submitted = {
        "requisition_id": application.requisition_id,
        "fields": {name: application.fields.get(name) for name in FIELDS},
        "contact": {"type": application.contact_type, "value": application.contact_value},
        "tracking_code": application.tracking_code,
        "consent": {
            "wording_version": application.consent.wording_version,
            "language": application.consent.language,
            "channels": list(application.consent.channels),
            "agreed_at": application.consent.agreed_at.isoformat(),
        },
    }
    capture = keep_original(
        conn,
        blobs,
        source=SOURCE,
        external_id=f"upload:{application.upload_id}" if application.upload_id else None,
        content=json.dumps(submitted, ensure_ascii=False, sort_keys=True).encode("utf-8"),
        media_type="application/json",
        extension="json",
        received_by=RECEIVED_BY,
    )
    if candidate_id is None:
        candidate_id = add_candidate(
            conn, capture.id, f"{SOURCE}:{capture.sha256.hex()}", RECEIVED_BY
        )

    add_fields(
        conn,
        candidate_id,
        _confirmed_fields(application),
        source="candidate_confirmed",
        source_ref=f"raw.capture:{capture.id}",
        recorded_by=RECEIVED_BY,
    )
    (row,) = conn.execute(
        _APPLICATION,
        {
            "opening": opening["id"],
            "candidate": candidate_id,
            "owner": opening["owner_recruiter"],
            "team": opening["team"],
            "by": RECEIVED_BY,
        },
    ).all()
    application_id = int(row.id)
    consent_id = consent_records.record(
        conn,
        candidate_id=candidate_id,
        application_id=application_id,
        wording_version=application.consent.wording_version,
        purposes=application.consent.purposes,
        channels=application.consent.channels,
        language=application.consent.language,
        agreed_at=application.consent.agreed_at,
        tracking_code=post["code"] if post else None,
    )
    return Received(application_id, candidate_id, consent_id)
