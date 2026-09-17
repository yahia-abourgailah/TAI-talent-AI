"""The website's CV upload (API plan section 8; B1, B2: BR-102, BR-106, BR-107).

No sign-in: candidates are strangers. So every request here is rate limited per client address,
the body size is checked before it is read, and the file type is checked on its content. Nothing a
candidate sent is ever repeated in an error, and no response is cached.

    POST /v1/public/cv-uploads               one file -> upload_id, upload_token, status
    GET  /v1/public/cv-uploads/{upload_id}   X-Upload-Token -> processing | ready | failed

The status never mentions hidden content (BR-308). The same file uploaded twice is one stored file
and one candidate; each upload still gets its own id and token, so a retried upload is harmless and
needs no Idempotency-Key.
"""

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any, Literal

from fastapi import (
    APIRouter,
    Depends,
    FastAPI,
    File,
    Header,
    Query,
    Request,
    Response,
    UploadFile,
)
from pydantic import BaseModel, Field
from sqlalchemy.engine import Connection

from api import idempotency
from api.deps import blob_store, db_connection
from api.errors import ApiError, error_response
from api.fields import Timestamp
from api.ids import decode, decode_filter, encode
from api.limits import APPLICATIONS, STATUS_CHECKS, UPLOADS, RateLimiter, Rule, client_address
from api.pages import DEFAULT_LIMIT, MAX_LIMIT, decode_cursor, next_cursor
from importer.blobs import BlobStore
from intake import consent as consent_records
from intake import files, public_apply, uploads
from pipeline.access import NotFound, Refused

router = APIRouter(prefix="/v1/public", tags=["public: careers page"])

UPLOAD_PATH = "/v1/public/cv-uploads"
PUBLIC_PREFIX = "/v1/public/"
# multipart framing around the file
_ENVELOPE_BYTES = 64 * 1024
# A public request that is not a file is a form: an application with its consent is a few
# kilobytes. Anything larger is refused before it is read into memory.
MAX_PUBLIC_BODY = 64 * 1024
_NO_STORE = {"Cache-Control": "no-store"}

Db = Annotated[Connection, Depends(db_connection)]
Blobs = Annotated[BlobStore, Depends(blob_store)]
Status = Literal["processing", "ready", "failed"]


class CvUploadOut(BaseModel):
    upload_id: str
    upload_token: str
    status: Status
    expires_at: Timestamp


class CvUploadStatusOut(BaseModel):
    upload_id: str
    status: Status
    expires_at: Timestamp
    # When ready: each form field as {"value", "inference", "language"}, or
    # {"value": null, "state": "not_recorded"}. Null otherwise.
    fields: dict[str, dict[str, Any]] | None


def _client(request: Request) -> str:
    """Whose request this is. Behind a proxy that is only knowable from a header we are told to
    trust: TALENT_TRUSTED_PROXY_HOPS, 0 by default."""
    return client_address(request, request.app.state.settings.trusted_proxy_hops)


def _limited(request: Request, rule: Rule) -> None:
    limiter: RateLimiter = request.app.state.rate_limiter
    wait = limiter.check(rule, _client(request))
    if wait is not None:
        raise ApiError(
            429,
            "rate_limited",
            "Too many requests. Try again later.",
            {"retry_after_seconds": wait},
            headers={"Retry-After": str(wait)},
        )


def install_upload_guard(app: FastAPI) -> None:
    """Refuses a public request by its headers, before the body is read: too big, or too often."""

    @app.middleware("http")
    async def guard_uploads(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        if request.method != "POST" or not request.url.path.startswith(PUBLIC_PREFIX):
            return await call_next(request)
        if request.url.path != UPLOAD_PATH:
            # Everything else a stranger may post is a form, and forms are small.
            length = request.headers.get("content-length")
            if length is not None and length.isdigit() and int(length) > MAX_PUBLIC_BODY:
                return error_response(request, 413, "body_too_large", "That request is too large.")
            return await call_next(request)
        wait = request.app.state.rate_limiter.check(UPLOADS, _client(request))
        if wait is not None:
            return error_response(
                request,
                429,
                "rate_limited",
                "Too many uploads. Try again later.",
                {"retry_after_seconds": wait},
                headers={"Retry-After": str(wait)},
            )
        length = request.headers.get("content-length")
        if length is None or not length.isdigit():
            return error_response(
                request, 411, "length_required", "Send the upload with a Content-Length header."
            )
        if int(length) > files.MAX_BYTES + _ENVELOPE_BYTES:
            return error_response(
                request,
                413,
                "file_too_large",
                f"The file is larger than {files.MAX_BYTES // (1024 * 1024)} MB.",
            )
        return await call_next(request)


@router.post("/cv-uploads", status_code=201)
def upload_cv(
    request: Request,
    response: Response,
    conn: Db,
    blobs: Blobs,
    file: Annotated[UploadFile, File(description="One CV: PDF, DOCX, JPEG or PNG, up to 10 MB")],
) -> CvUploadOut:
    """Keeps the CV and starts reading it. Poll the status with the returned token."""
    content = file.file.read(files.MAX_BYTES + 1)
    if len(content) > files.MAX_BYTES:
        raise ApiError(413, "file_too_large", "The file is larger than 10 MB.")
    if not content:
        raise ApiError(400, "invalid_request", "The file is empty.")
    kind = files.sniff(content)
    if kind is None:
        raise ApiError(
            415,
            "unsupported_file_type",
            "Upload a PDF, DOCX, JPEG or PNG file.",
            {"accepted": list(files.ACCEPTED)},
        )
    received = uploads.receive_cv(conn, blobs, content, kind)
    status = uploads.upload_status(conn, received.upload_id, received.token)
    assert status is not None
    response.headers.update(_NO_STORE)
    return CvUploadOut(
        upload_id=encode("upload", received.upload_id),
        upload_token=received.token,
        status=status["status"],
        expires_at=received.expires_at,
    )


@router.get("/cv-uploads/{upload_id}")
def cv_upload_status(
    upload_id: str,
    request: Request,
    response: Response,
    conn: Db,
    token: Annotated[str, Header(alias="X-Upload-Token", min_length=1, max_length=200)],
) -> CvUploadStatusOut:
    """processing, ready (with the form to check) or failed (show an empty form). A wrong or
    expired token reads as not found."""
    _limited(request, STATUS_CHECKS)
    found = uploads.upload_status(conn, decode("upload", upload_id), token)
    if found is None:
        raise NotFound("Upload not found.")
    response.headers.update(_NO_STORE)
    return CvUploadStatusOut(
        upload_id=encode("upload", found["upload_id"]),
        status=found["status"],
        expires_at=found["expires_at"],
        fields=found["fields"],
    )


# --- The jobs a careers page may show (BR-401) ----------------------------------------------------


class PublicRequisitionOut(BaseModel):
    requisition_id: str
    title: str
    brand: str
    department: str
    location: str | None
    track: str


class PublicRequisitionPage(BaseModel):
    items: list[PublicRequisitionOut]
    next_cursor: str | None


def _requisition(row: dict[str, Any]) -> PublicRequisitionOut:
    return PublicRequisitionOut(
        requisition_id=encode("requisition", row["id"]),
        title=row["title"],
        brand=row["brand"],
        department=row["department"],
        location=row["location"],
        track=row["track"],
    )


@router.get("/requisitions")
def public_requisitions(
    request: Request,
    response: Response,
    conn: Db,
    limit: Annotated[int, Query(ge=1, le=MAX_LIMIT)] = DEFAULT_LIMIT,
    cursor: Annotated[str | None, Query(max_length=200)] = None,
) -> PublicRequisitionPage:
    """Open jobs a candidate may apply to. Public fields only."""
    _limited(request, STATUS_CHECKS)
    rows = public_apply.list_open_requisitions(conn, limit + 1, decode_cursor(cursor))
    response.headers.update(_NO_STORE)
    return PublicRequisitionPage(
        items=[_requisition(row) for row in rows[:limit]], next_cursor=next_cursor(rows, limit)
    )


@router.get("/requisitions/{requisition_id}")
def public_requisition(
    requisition_id: str, request: Request, response: Response, conn: Db
) -> PublicRequisitionOut:
    """One open job, for the page a job-post link opens."""
    _limited(request, STATUS_CHECKS)
    row = public_apply.open_requisition(conn, decode("requisition", requisition_id))
    response.headers.update(_NO_STORE)
    return _requisition(row)


# --- The words a candidate agrees to (CR-02) ------------------------------------------------------


class ConsentWordingOut(BaseModel):
    wording_version: str
    provisional: bool
    purposes: list[str]
    text_ar: str
    text_en: str


@router.get("/consent-wording")
def consent_wording(request: Request, response: Response, conn: Db) -> ConsentWordingOut:
    """The approved consent text, in Arabic and English. Show exactly this text, and send its
    version back with the application."""
    _limited(request, STATUS_CHECKS)
    wording = consent_records.wording_in_force(conn)
    response.headers.update(_NO_STORE)
    return ConsentWordingOut(
        wording_version=wording["version"],
        provisional=wording["provisional"],
        purposes=list(wording["purposes"]),
        text_ar=wording["text_ar"],
        text_en=wording["text_en"],
    )


# --- Applying (BR-101, BR-109, BR-602, CR-02) -----------------------------------------------------

FieldName = Annotated[str, Field(pattern=r"^[a-z][a-z_]{1,40}$")]
FieldText = Annotated[str, Field(max_length=500)]
AGREED_AT_AHEAD = timedelta(hours=1)


class ContactChannelIn(BaseModel):
    type: Literal["phone", "whatsapp"]
    value: Annotated[str, Field(min_length=6, max_length=40, pattern=r"\S")]


class ConsentIn(BaseModel):
    agreed: bool
    wording_version: Annotated[str, Field(max_length=64)]
    purposes: list[Annotated[str, Field(max_length=64)]] = []
    channels: list[Literal["phone", "whatsapp", "email"]]
    language: Literal["ar", "en"]
    agreed_at: datetime


class PublicApplicationIn(BaseModel):
    requisition_id: Annotated[str, Field(max_length=40)]
    upload_id: Annotated[str, Field(max_length=40)] | None = None
    fields: dict[FieldName, FieldText | None] = {}
    contact_channel: ContactChannelIn
    consent: ConsentIn
    tracking_code: Annotated[str, Field(max_length=40)] | None = None


class ApplicationReceivedOut(BaseModel):
    application_id: str
    status: Literal["received"]


@router.post("/applications", status_code=201, response_model=ApplicationReceivedOut)
def apply(
    body: PublicApplicationIn,
    request: Request,
    conn: Db,
    blobs: Blobs,
    token: Annotated[str | None, Header(alias="X-Upload-Token", max_length=200)] = None,
    key: idempotency.KeyHeader = None,
) -> Any:
    """Sends one application. A phone or WhatsApp number and an agreement are required. The answer
    carries the application id only: never a score, a tier or a gate result."""
    _limited(request, APPLICATIONS)
    if key is None:
        raise ApiError(
            400,
            "idempotency_key_required",
            "Send an Idempotency-Key (a UUID) with an application, so a retry is harmless.",
        )
    if body.consent.agreed_at.tzinfo is None:
        raise ApiError(
            400,
            "invalid_request",
            "consent.agreed_at needs a time zone, for example 2026-10-05T09:00:00Z.",
        )
    if body.consent.agreed_at > datetime.now(UTC) + AGREED_AT_AHEAD:
        raise ApiError(400, "invalid_request", "consent.agreed_at is in the future.")

    received = public_apply.Application(
        requisition_id=decode("requisition", body.requisition_id),
        fields=dict(body.fields),
        contact_type=body.contact_channel.type,
        contact_value=body.contact_channel.value,
        consent=public_apply.Consent(
            agreed=body.consent.agreed,
            wording_version=body.consent.wording_version,
            purposes=tuple(body.consent.purposes),
            channels=tuple(body.consent.channels),
            language=body.consent.language,
            agreed_at=body.consent.agreed_at,
        ),
        upload_id=decode_filter("upload", "upload_id", body.upload_id),
        upload_token=token,
        tracking_code=body.tracking_code,
    )

    def send() -> ApplicationReceivedOut:
        try:
            done = public_apply.apply(conn, blobs, received)
        except consent_records.ConsentRefused as refusal:
            raise ApiError(400, refusal.code, str(refusal)) from None
        except Refused as refusal:
            # What the sender can fix is a bad request; anything else stays a refusal (409).
            if refusal.code in public_apply.BAD_REQUEST_CODES:
                raise ApiError(400, refusal.code, str(refusal)) from None
            raise
        return ApplicationReceivedOut(
            application_id=encode("application", done.application_id), status="received"
        )

    answer = idempotency.respond(conn, request, "public", key, body, 201, send)
    answer.headers.update(_NO_STORE)
    return answer
