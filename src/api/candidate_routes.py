"""Candidates and their evaluations (API plan section 6; BR-201, BR-202, BR-303, BR-307, BR-408).

A candidate's fields each say where they came from and whether anyone verified them. A field
nobody recorded is {"value": null, "state": "not_recorded"}: never a guess. Lists carry summaries
only; fetch one candidate for its fields. Nothing here is ever in a URL but ids.

An evaluation explains a verdict without re-running anything: the criteria version, the outcome,
the score and tier, and the signals and flags behind them. A failed gate is not a rejection.
"""

from collections.abc import Mapping
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Query, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy.engine import Connection

from api import idempotency
from api.deps import blob_store, current_principal, db_connection
from api.errors import ApiError, request_id
from api.fields import Timestamp
from api.ids import decode, decode_filter, encode
from api.pages import DEFAULT_LIMIT, MAX_LIMIT, decode_cursor, next_cursor
from auth import Principal
from candidates import documents, reads
from importer.blobs import BlobStore
from intake import manual
from intake.files import EXTENSION
from pipeline.access import actor_from_principal, reader_from_principal
from scoring.explain import explain

router = APIRouter(prefix="/v1")

Signed = Annotated[Principal, Depends(current_principal)]
Db = Annotated[Connection, Depends(db_connection)]
Blobs = Annotated[BlobStore, Depends(blob_store)]


# Where a candidate's record came from. The names are the raw capture's own, so they are the same
# words the source field carries back.
SourceName = Literal["tai_master", "cv_upload", "manual_entry", "public_apply"]


class CandidateSummaryOut(BaseModel):
    id: str
    # The name as it stands, so a list says who each row is about. Null when nobody recorded one.
    full_name: str | None
    source: str
    created_at: Timestamp
    archived: bool


class CandidatePage(BaseModel):
    items: list[CandidateSummaryOut]
    next_cursor: str | None


class CandidateOut(BaseModel):
    id: str
    source: str
    created_at: Timestamp
    archived_at: Timestamp | None
    archived_reason: str | None
    fields: dict[str, dict[str, Any]]


class EvaluationOut(BaseModel):
    id: str
    candidate_id: str
    criteria_version: str
    origin: str
    model_version: str | None
    prompt_version: str | None
    track: str
    outcome: str
    score: float | None
    tier: str | None
    recommendation: str | None
    call_priority: str | None
    signals: list[str]
    flags: list[str]
    evaluated_at: Timestamp | None
    recorded_at: Timestamp


class ScorePartOut(BaseModel):
    part: str
    says: str
    points: int
    # The most this part can come to on this track, so a number has something to be read against.
    # 0 where the part is only ever a penalty.
    out_of: int


class ExplanationOut(BaseModel):
    evaluation_id: str
    criteria_version: str
    track: str
    stored_score: float | None
    stored_tier: str | None
    parts: list[ScorePartOut]
    other_adjustments: int
    total: int
    tier: str
    disqualified: bool
    disqualify_reason: str | None
    signals: list[str]
    flags: list[str]
    # False when the record has changed since: the stored score was reached with what we knew then.
    matches_stored: bool


class EvaluationList(BaseModel):
    items: list[EvaluationOut]


def _field(row: Mapping[str, Any]) -> dict[str, Any]:
    if row["verification_status"] == "not_recorded":
        return {"value": None, "state": "not_recorded"}
    shown: dict[str, Any] = {
        "value": row["value"],
        "source": row["source"],
        "verification": row["verification_status"],
        "verified_at": None if row["verified_at"] is None else row["verified_at"].isoformat(),
    }
    if row["inference"] is not None:
        shown["inference"] = row["inference"]
    if row.get("verified_by") is not None:
        shown["verified_by"] = row["verified_by"]
    if row.get("language") is not None:
        shown["language"] = row["language"]
    return shown


def _evaluation(row: dict[str, Any]) -> EvaluationOut:
    return EvaluationOut(
        id=encode("evaluation", row["id"]),
        candidate_id=encode("candidate", row["candidate_id"]),
        criteria_version=row["criteria_version_id"],
        origin=row["origin"],
        model_version=row["model_version"],
        prompt_version=row["prompt_version"],
        track=reads.track(row),
        outcome=reads.outcome(row),
        score=reads.score(row),
        tier=row["tier"],
        recommendation=row["recommendation"],
        call_priority=row["call_priority"],
        signals=list(row["signals"] or []),
        flags=list(row["flags"] or []),
        evaluated_at=row["evaluated_at"],
        recorded_at=row["recorded_at"],
    )


@router.get("/candidates", tags=["candidates"])
def list_candidates(
    principal: Signed,
    conn: Db,
    limit: Annotated[int, Query(ge=1, le=MAX_LIMIT)] = DEFAULT_LIMIT,
    cursor: Annotated[str | None, Query(max_length=200)] = None,
    requisition_id: Annotated[str | None, Query(max_length=40)] = None,
    owner_id: Annotated[str | None, Query(max_length=200)] = None,
    source: Annotated[
        list[SourceName] | None,
        Query(description="Where the record came from; repeat for more than one"),
    ] = None,
    archived: Annotated[
        bool | None,
        Query(description="true: only archived records; false: only the ones still in play"),
    ] = None,
) -> CandidatePage:
    """Candidates in your scope, newest first, as summaries.

    `source` narrows by where a record came from: `?source=cv_upload&source=public_apply` is the
    people who have arrived since the migration, without the 5,140 that came from the sheet.
    """
    rows = reads.list_candidates(
        conn,
        actor_from_principal(principal),
        limit=limit + 1,
        before=decode_cursor(cursor),
        opening_id=decode_filter("requisition", "requisition_id", requisition_id),
        owner=owner_id,
        sources=[str(name) for name in source] if source else None,
        archived=archived,
    )
    items = [
        CandidateSummaryOut(
            id=encode("candidate", row["id"]),
            full_name=row.get("full_name"),
            source=row["source"],
            created_at=row["created_at"],
            archived=row["archived_at"] is not None,
        )
        for row in rows[:limit]
    ]
    return CandidatePage(items=items, next_cursor=next_cursor(rows, limit))


@router.get("/candidates/{candidate_id}", tags=["candidates"])
def get_candidate(candidate_id: str, principal: Signed, conn: Db) -> CandidateOut:
    """One candidate, every field with its source and verification (BR-201, BR-202)."""
    row = reads.get_candidate(
        conn, actor_from_principal(principal), decode("candidate", candidate_id)
    )
    return CandidateOut(
        id=encode("candidate", row["id"]),
        source=row["source"],
        created_at=row["created_at"],
        archived_at=row["archived_at"],
        archived_reason=row["archived_reason"],
        fields={field["field"]: _field(field) for field in row["fields"]},
    )


@router.get("/candidates/{candidate_id}/evaluations", tags=["evaluations"])
def list_evaluations(candidate_id: str, principal: Signed, conn: Db) -> EvaluationList:
    """Every evaluation of a candidate, newest first (BR-303)."""
    actor = reader_from_principal(principal)
    rows = reads.list_evaluations(conn, actor, decode("candidate", candidate_id))
    return EvaluationList(items=[_evaluation(row) for row in rows])


@router.get("/evaluations/{evaluation_id}", tags=["evaluations"])
def get_evaluation(evaluation_id: str, principal: Signed, conn: Db) -> EvaluationOut:
    """One evaluation, with everything needed to explain it (BR-307, CR-04)."""
    actor = reader_from_principal(principal)
    return _evaluation(reads.get_evaluation(conn, actor, decode("evaluation", evaluation_id)))


@router.get("/evaluations/{evaluation_id}/explanation", tags=["evaluations"])
def explain_evaluation(evaluation_id: str, principal: Signed, conn: Db) -> ExplanationOut:
    """Where the score came from, part by part.

    The evaluation keeps the score, the tier and the reasons in words; the arithmetic behind them
    is not kept, so the same criteria version is run again over what is recorded about the
    candidate now. Nothing is written. When a field has been corrected since, the total here will
    differ from the stored one and `matches_stored` says so: the score on record was reached with
    what we knew then, and it stays the decision.
    """
    actor = reader_from_principal(principal)
    evaluation = reads.get_evaluation(conn, actor, decode("evaluation", evaluation_id))
    candidate = reads.get_candidate(conn, actor, int(evaluation["candidate_id"]))
    # The reads layer hands back one row per field, each with where it came from.
    values = {str(row["field"]): row["value"] for row in candidate["fields"]}
    try:
        found = explain(values, str(evaluation["criteria_version_id"]), reads.track(evaluation))
    except KeyError as unknown:
        raise ApiError(409, "no_scorer", str(unknown)) from None
    stored = reads.score(evaluation)
    return ExplanationOut(
        evaluation_id=encode("evaluation", evaluation["id"]),
        stored_score=stored,
        stored_tier=evaluation["tier"],
        matches_stored=stored is not None and int(stored) == found.total,
        **found.as_dict(),
    )


class CandidateSearchIn(BaseModel):
    full_name: Annotated[str, Field(min_length=2, max_length=200)] | None = None
    email: Annotated[str, Field(min_length=3, max_length=254)] | None = None
    phone: Annotated[str, Field(min_length=10, max_length=40)] | None = None
    limit: int = Field(default=20, ge=1, le=50)


@router.post("/candidates/search", tags=["candidates"])
def search_candidates(body: CandidateSearchIn, principal: Signed, conn: Db) -> CandidatePage:
    """Finds candidates in your scope by full name, email or phone, sent in the body and never in
    the URL. Every value sent must match. Matching is exact once case, spacing and phone formats are
    tidied; it is not a fuzzy or duplicate search. Returns summaries, newest first."""
    actor = actor_from_principal(principal)
    try:
        criteria = reads.SearchCriteria.build(
            full_name=body.full_name, email=body.email, phone=body.phone
        )
    except ValueError as exc:
        raise ApiError(400, "invalid_request", str(exc)) from None
    rows = reads.search_candidates(conn, actor, criteria, limit=body.limit)
    items = [
        CandidateSummaryOut(
            id=encode("candidate", row["id"]),
            full_name=None,  # a search answers with no values of its own
            source=row["source"],
            created_at=row["created_at"],
            archived=row["archived_at"] is not None,
        )
        for row in rows
    ]
    return CandidatePage(items=items, next_cursor=None)


# --- Typed in by a recruiter, and checked by a person (BR-103, BR-202) --------------------------

FieldName = Literal[
    "full_name",
    "phone",
    "whatsapp",
    "email",
    "location",
    "current_title",
    "current_employer",
    "education",
    "graduation_year",
    "years_experience",
    "age",
    "profile_url",
]
FieldText = Annotated[str, Field(max_length=500)]


class CandidateEntryIn(BaseModel):
    fields: dict[FieldName, FieldText | None] = Field(min_length=1)


class EnteredCandidateOut(BaseModel):
    id: str
    source: str
    created_at: Timestamp
    archived: bool
    review_item_id: str | None


class FieldCheckIn(BaseModel):
    # The value the person checked. Leave it out to confirm the value on record.
    value: Annotated[str, Field(min_length=1, max_length=500, pattern=r"\S")] | None = None


class FieldCheckOut(BaseModel):
    candidate_id: str
    field: str
    source: str
    verification: str
    verified_at: Timestamp
    verified_by: str
    corrected: bool
    # The candidate's unverified_candidate review item, when this check closed it.
    resolved_review_item_id: str | None


@router.post(
    "/candidates", status_code=201, response_model=EnteredCandidateOut, tags=["candidates"]
)
def enter_candidate(
    body: CandidateEntryIn,
    request: Request,
    principal: Signed,
    conn: Db,
    blobs: Blobs,
    key: idempotency.KeyHeader = None,
) -> JSONResponse:
    """A candidate you found, typed in by hand. Every field is recorded as manual and unchecked,
    with your name, and the candidate joins the review queue until a person checks them. Fields
    left out or empty are not recorded. The response carries ids only."""
    actor = actor_from_principal(principal)

    def create() -> EnteredCandidateOut:
        try:
            values = {str(name): value for name, value in body.fields.items()}
            entered = manual.enter_candidate(conn, blobs, actor, values)
        except ValueError as exc:
            raise ApiError(400, "invalid_request", str(exc)) from None
        row = reads.get_candidate(conn, actor, entered.candidate_id)
        item = entered.review_item_id
        return EnteredCandidateOut(
            id=encode("candidate", row["id"]),
            source=row["source"],
            created_at=row["created_at"],
            archived=row["archived_at"] is not None,
            review_item_id=None if item is None else encode("review_item", item),
        )

    return idempotency.respond(conn, request, principal.subject, key, body, 201, create)


@router.post(
    "/candidates/{candidate_id}/fields/{field}/verification", status_code=201, tags=["candidates"]
)
def verify_field(
    candidate_id: str, field: FieldName, body: FieldCheckIn, principal: Signed, conn: Db
) -> FieldCheckOut:
    """You checked one field. Records a new, verified row with your name and the time; the old
    row stays. Send `value` when you corrected it. Once no field is left unchecked, the
    candidate's unverified_candidate review item is resolved as checked, by you."""
    actor = actor_from_principal(principal)
    number = decode("candidate", candidate_id)
    row = manual.verify_field(conn, actor, number, field, body.value)
    closed = manual.close_checked_candidate(conn, actor, number)
    return FieldCheckOut(
        candidate_id=encode("candidate", number),
        field=row["field"],
        source=row["source"],
        verification=row["verification_status"],
        verified_at=row["verified_at"],
        verified_by=row["verified_by"],
        corrected=row["corrected"],
        resolved_review_item_id=None if closed is None else encode("review_item", closed),
    )


# --- A candidate's files (BR-107) -----------------------------------------------------------------


class ReadingOut(BaseModel):
    status: Literal["processing", "read", "failed"]
    failure: str | None
    hidden_content: bool
    finished_at: Timestamp | None


class DocumentOut(BaseModel):
    id: str
    candidate_id: str
    media_type: str
    byte_size: int
    received_at: Timestamp
    reading: ReadingOut


class DocumentList(BaseModel):
    items: list[DocumentOut]


@router.get("/candidates/{candidate_id}/documents", tags=["documents"])
def list_documents(
    candidate_id: str, request: Request, principal: Signed, conn: Db
) -> DocumentList:
    """The candidate's CVs, oldest first, with how reading each one ended. Every listing is
    recorded with your name."""
    actor = actor_from_principal(principal)
    number = decode("candidate", candidate_id)
    rows = documents.list_documents(conn, actor, number, request_id(request))
    return DocumentList(
        items=[
            DocumentOut(
                id=encode("document", row["id"]),
                candidate_id=encode("candidate", number),
                media_type=row["media_type"],
                byte_size=row["byte_size"],
                received_at=row["received_at"],
                reading=ReadingOut(
                    status=row["reading"] or "processing",
                    failure=row["reading_failure"],
                    hidden_content=row["hidden_content"],
                    finished_at=row["read_at"],
                ),
            )
            for row in rows
        ]
    )


@router.get(
    "/documents/{document_id}/file",
    tags=["documents"],
    response_class=Response,
    responses={200: {"description": "The file, byte for byte as uploaded"}},
)
def download_document(
    document_id: str, request: Request, principal: Signed, conn: Db, blobs: Blobs
) -> Response:
    """The original file, exactly as it was uploaded. Every download is recorded with your name."""
    actor = actor_from_principal(principal)
    number = decode("document", document_id)
    found = documents.open_document(conn, actor, number, request_id(request))
    content = blobs.get(found["blob_key"])
    if content is None:
        raise ApiError(503, "unavailable", "The file store did not return the file. Try again.")
    filename = f"{encode('document', number)}.{EXTENSION.get(found['media_type'], 'bin')}"
    return Response(
        content,
        media_type=found["media_type"],
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )
