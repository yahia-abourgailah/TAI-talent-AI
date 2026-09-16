"""What a candidate agreed to, and the exact words they were shown (BR-101, CR-02).

A consent record is written when the candidate applies, never before and never afterwards by hand.
It keeps the wording version they saw, what it covers, the channels they allowed, the language of
the page, when they agreed and when we received it. Rows are append-only: withdrawing consent is a
later record, never an edit (BR-504, week 6 slice 2).

The wording itself is versioned like the step list: a new wording is a new version, and the one in
force is the latest activation. The wording in force is provisional until Legal approves one
(D-WEB-4); the platform still records which version each candidate saw.
"""

from collections.abc import Sequence
from datetime import datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Connection

from pipeline.access import run

PURPOSES = ("recruitment_contact",)
CHANNELS = ("phone", "whatsapp", "email")
LANGUAGES = ("ar", "en")
SOURCE_PUBLIC = "public_apply"


class ConsentRefused(Exception):
    """The consent sent with an application cannot be recorded. `code` is stable for the API."""

    def __init__(self, message: str, code: str) -> None:
        super().__init__(message)
        self.code = code


def wording_in_force(conn: Connection) -> dict[str, Any]:
    """The wording to show, and to check an application against."""
    rows = run(
        conn,
        text(
            "SELECT version, provisional, purposes, text_ar, text_en, source "
            "FROM core.consent_wording WHERE version = core.consent_wording_in_force()"
        ),
        {},
    )
    if not rows:
        raise ConsentRefused("No consent wording is in force.", "consent_wording_missing")
    return rows[0]


def record(
    conn: Connection,
    *,
    candidate_id: int,
    application_id: int | None,
    wording_version: str,
    purposes: Sequence[str],
    channels: Sequence[str],
    language: str,
    agreed_at: datetime,
    source: str = SOURCE_PUBLIC,
    tracking_code: str | None = None,
) -> int:
    """Records one consent. Refuses anything the candidate was not actually shown."""
    in_force = wording_in_force(conn)
    if wording_version != in_force["version"]:
        raise ConsentRefused(
            "The consent wording has changed. Fetch it again and ask the candidate again.",
            "consent_wording_changed",
        )
    allowed = set(in_force["purposes"])
    asked = list(dict.fromkeys(purposes)) or list(allowed)
    if not set(asked) <= allowed:
        raise ConsentRefused(
            "The consent covers purposes the wording does not name.", "consent_purposes_unknown"
        )
    picked = list(dict.fromkeys(channels))
    if not picked or not set(picked) <= set(CHANNELS):
        raise ConsentRefused(
            f"Choose at least one channel from: {', '.join(CHANNELS)}.", "consent_channels_invalid"
        )
    if language not in LANGUAGES:
        raise ConsentRefused("The page language is ar or en.", "consent_language_invalid")

    (row,) = run(
        conn,
        text(
            """
            INSERT INTO core.consent
              (candidate_id, application_id, wording_version, purposes, channels, language,
               agreed_at, source, tracking_code)
            VALUES
              (:candidate, :application, :version, :purposes, :channels, :language, :agreed_at,
               :source, :code)
            RETURNING id
            """
        ),
        {
            "candidate": candidate_id,
            "application": application_id,
            "version": wording_version,
            "purposes": asked,
            "channels": picked,
            "language": language,
            "agreed_at": agreed_at,
            "source": source,
            "code": tracking_code,
        },
    )
    return int(row["id"])


def consents_of(conn: Connection, candidate_id: int) -> list[dict[str, Any]]:
    """Every consent a candidate has given, newest first. No candidate values."""
    return run(
        conn,
        text(
            "SELECT id, application_id, wording_version, purposes, channels, language, agreed_at, "
            "received_at, source, tracking_code FROM core.consent "
            "WHERE candidate_id = :id ORDER BY id DESC"
        ),
        {"id": candidate_id},
    )
