"""Reading candidates and their evaluations, scoped to the person asking (BR-201, BR-303, BR-408).

A recruiter sees a candidate when they own one of the candidate's applications, or typed the
candidate in themselves; a TA lead or an admin sees every candidate. Every field says where it
came from and whether anyone verified it; a field nobody recorded is not recorded, never a guess
(BR-703). Out of scope reads as not found.

Evaluations are read with the same scope, except that the criteria owner reads all of them.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Self

from sqlalchemy import text
from sqlalchemy.engine import Connection

from pipeline.access import Actor, NotFound, not_locked, run

CANDIDATE_NOT_FOUND = "Candidate not found."
EVALUATION_NOT_FOUND = "Evaluation not found."

# The name as it stands today, for a list of your own candidates. A list carries no field with its
# provenance — that is what fetching one candidate is for — but a page of bare ids is not something
# a person can work from, and the name is the one value that says who each row is about.
#
# Not in a search answer. A search is given values and must not hand any back: someone who guesses
# an address should learn nothing from the guess but whether they may open the record.
NAME = """
    (SELECT f.value FROM core.candidate_field_current f
     WHERE f.candidate_id = c.id AND f.field = 'full_name') AS full_name
"""

VISIBLE = f"""
    {not_locked("c.id")} AND
    (:sees_all OR EXISTS (
       SELECT 1 FROM pipeline.application a
       WHERE a.candidate_id = c.id AND a.owner_recruiter = :subject
    ) OR (
       c.created_by = :subject AND EXISTS (
         SELECT 1 FROM raw.capture m WHERE m.id = c.capture_id AND m.source = 'manual_entry'
       )
    ))
"""

_EVALUATION = (
    "e.id, e.candidate_id, e.application_id, e.criteria_version_id, e.origin, e.score, e.tier, "
    "e.recommendation, "
    "e.call_priority, e.signals, e.flags, e.flags_text, e.model_version, e.prompt_version, "
    "e.evaluated_at, e.recorded_at"
)


def _visible(conn: Connection, actor: Actor, candidate_id: int) -> dict[str, Any]:
    rows = run(
        conn,
        text(
            f"""
            SELECT c.id, c.created_at, c.archived_at, c.archived_reason, r.source
            FROM core.candidate c JOIN raw.capture r ON r.id = c.capture_id
            WHERE c.id = :id AND {VISIBLE}
            """
        ),
        {"id": candidate_id, **actor.scope()},
    )
    if not rows:
        raise NotFound(CANDIDATE_NOT_FOUND)
    return rows[0]


def get_candidate(conn: Connection, actor: Actor, candidate_id: int) -> dict[str, Any]:
    candidate = _visible(conn, actor, candidate_id)
    candidate["fields"] = run(
        conn,
        text(
            "SELECT field, value, source, verification_status, verified_at, verified_by, "
            "inference, language "
            "FROM core.candidate_field_current WHERE candidate_id = :id ORDER BY field"
        ),
        {"id": candidate_id},
    )
    return candidate


def list_candidates(
    conn: Connection,
    actor: Actor,
    *,
    limit: int,
    before: int | None = None,
    opening_id: int | None = None,
    owner: str | None = None,
    sources: Sequence[str] | None = None,
    archived: bool | None = None,
) -> list[dict[str, Any]]:
    """Newest first. Summaries: the name, and where the record came from. No fields.

    `sources` narrows by where the record came from — the 5,140 migrated from the sheet
    (tai_master) drown out everyone who has arrived since, and a recruiter's working list is
    usually the people who applied, not the backlog.

    `archived` leaves out (or shows only) the records somebody archived with a reason. Archiving is
    how a record is taken out of the way without being lost (BR-205), so a list that still shows
    them has not really put anything away.
    """
    return run(
        conn,
        text(
            f"""
            SELECT c.id, c.created_at, c.archived_at, r.source, {NAME}
            FROM core.candidate c JOIN raw.capture r ON r.id = c.capture_id
            WHERE {VISIBLE}
              AND (CAST(:sources AS text[]) IS NULL OR r.source = ANY(:sources))
              AND (CAST(:archived AS boolean) IS NULL
                   OR (c.archived_at IS NOT NULL) = :archived)
              AND (CAST(:before AS bigint) IS NULL OR c.id < :before)
              AND (CAST(:opening AS bigint) IS NULL OR EXISTS (
                SELECT 1 FROM pipeline.application o
                WHERE o.candidate_id = c.id AND o.opening_id = :opening
                  AND (:sees_all OR o.owner_recruiter = :subject)
              ))
              AND (CAST(:owner AS text) IS NULL OR EXISTS (
                SELECT 1 FROM pipeline.application w
                WHERE w.candidate_id = c.id AND w.owner_recruiter = :owner
              ))
            ORDER BY c.id DESC LIMIT :limit
            """
        ),
        {
            "limit": limit,
            "before": before,
            "opening": opening_id,
            "owner": owner,
            "sources": list(sources) if sources else None,
            "archived": archived,
            **actor.scope(),
        },
    )


def list_evaluations(conn: Connection, actor: Actor, candidate_id: int) -> list[dict[str, Any]]:
    """Every evaluation of one candidate, newest first."""
    _visible(conn, actor, candidate_id)
    return run(
        conn,
        text(
            f"SELECT {_EVALUATION} FROM core.evaluation e WHERE e.candidate_id = :id "
            "ORDER BY coalesce(e.evaluated_at, e.recorded_at) DESC, e.id DESC"
        ),
        {"id": candidate_id},
    )


def get_evaluation(conn: Connection, actor: Actor, evaluation_id: int) -> dict[str, Any]:
    rows = run(
        conn,
        text(
            f"""
            SELECT {_EVALUATION} FROM core.evaluation e
            JOIN core.candidate c ON c.id = e.candidate_id
            WHERE e.id = :id AND {VISIBLE}
            """
        ),
        {"id": evaluation_id, **actor.scope()},
    )
    if not rows:
        raise NotFound(EVALUATION_NOT_FOUND)
    return rows[0]


def outcome(evaluation: dict[str, Any]) -> str:
    """failed_gate when a disqualification is recorded, otherwise passed. A failed gate is never a
    rejection: a person confirms it (BR-405).

    An assessment of a job that is not sales passes no gate and fails none: no rules ran, so
    "passed" would be a verdict nobody reached (BR-305, BR-703).
    """
    if evaluation.get("origin") == "ai":
        return "not_applicable"
    flags = evaluation.get("flags") or []
    flagged = any(str(flag).startswith("DISQUALIFIED") for flag in flags)
    return (
        "failed_gate"
        if flagged or "DISQUALIFIED" in (evaluation.get("flags_text") or "")
        else "passed"
    )


def track(evaluation: dict[str, Any]) -> str:
    """Track B verdicts carry T tiers (replay.mapping.track).

    An assessment of a job that is not sales is on neither track: no rules ran, so there is no
    track to name and none is invented (BR-703).
    """
    if evaluation.get("origin") == "ai":
        return "not_applicable"
    return "headhunt" if str(evaluation.get("tier") or "").upper().startswith("T") else "entry"


def score(evaluation: dict[str, Any]) -> float | None:
    value = evaluation.get("score")
    return float(value) if isinstance(value, Decimal | int | float) else None


# --- Search (API plan section 6): values in the body, exact matches, in scope --------------------

# Arabic-Indic and extended Arabic-Indic digits, read as 0-9.
_OTHER_DIGITS = "".join(chr(0x0660 + i) for i in range(10)) + "".join(
    chr(0x06F0 + i) for i in range(10)
)
_ASCII_DIGITS = "0123456789" * 2
_DIGITS = str.maketrans(_OTHER_DIGITS, _ASCII_DIGITS)
PHONE_DIGITS = 10


@dataclass(frozen=True, slots=True)
class SearchCriteria:
    """What a search matches on, tidied: names and emails lower-cased with single spaces, phones as
    their last 10 digits, so +20 100..., 0020 100... and 0100... are one number."""

    full_name: str | None
    email: str | None
    phone: str | None

    @classmethod
    def build(cls, *, full_name: str | None, email: str | None, phone: str | None) -> Self:
        name = " ".join(full_name.split()).lower() if full_name and full_name.strip() else None
        mail = email.strip().lower() if email and email.strip() else None
        digits = None
        if phone and phone.strip():
            only = "".join(ch for ch in phone.translate(_DIGITS) if ch in "0123456789")
            if len(only) < PHONE_DIGITS:
                raise ValueError(f"phone needs at least {PHONE_DIGITS} digits.")
            digits = only[-PHONE_DIGITS:]
        if mail is not None and "@" not in mail:
            raise ValueError("email must contain @.")
        if name is None and mail is None and digits is None:
            raise ValueError("Send at least one of full_name, email or phone.")
        return cls(name, mail, digits)


def search_candidates(
    conn: Connection, actor: Actor, criteria: SearchCriteria, *, limit: int
) -> list[dict[str, Any]]:
    """Candidates in scope whose current fields match every value given. Newest first."""
    return run(
        conn,
        text(
            rf"""
            SELECT c.id, c.created_at, c.archived_at, r.source
            FROM core.candidate c JOIN raw.capture r ON r.id = c.capture_id
            WHERE {VISIBLE}
              AND (CAST(:name AS text) IS NULL OR EXISTS (
                SELECT 1 FROM core.candidate_field_current f
                WHERE f.candidate_id = c.id AND f.field = 'full_name'
                  AND lower(regexp_replace(btrim(f.value), '\s+', ' ', 'g')) = :name
              ))
              AND (CAST(:email AS text) IS NULL OR EXISTS (
                SELECT 1 FROM core.candidate_field_current f
                WHERE f.candidate_id = c.id AND f.field = 'email'
                  AND lower(btrim(f.value)) = :email
              ))
              AND (CAST(:phone AS text) IS NULL OR EXISTS (
                SELECT 1 FROM core.candidate_field_current f
                WHERE f.candidate_id = c.id AND f.field = 'phone'
                  AND right(regexp_replace(translate(f.value, :other_digits, :ascii_digits),
                                           '[^0-9]', '', 'g'), :phone_digits) = :phone
              ))
            ORDER BY c.id DESC LIMIT :limit
            """
        ),
        {
            "name": criteria.full_name,
            "email": criteria.email,
            "phone": criteria.phone,
            "other_digits": _OTHER_DIGITS,
            "ascii_digits": _ASCII_DIGITS,
            "phone_digits": PHONE_DIGITS,
            "limit": limit,
            **actor.scope(),
        },
    )
