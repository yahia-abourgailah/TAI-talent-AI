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
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, FastAPI, File, Header, Request, Response, UploadFile
from pydantic import BaseModel
from sqlalchemy.engine import Connection

from api.deps import blob_store, db_connection
from api.errors import ApiError, error_response
from api.fields import Timestamp
from api.ids import decode, encode
from api.limits import STATUS_CHECKS, UPLOADS, RateLimiter, Rule
from importer.blobs import BlobStore
from intake import files, uploads
from pipeline.access import NotFound

router = APIRouter(prefix="/v1/public", tags=["public: careers page"])

UPLOAD_PATH = "/v1/public/cv-uploads"
# multipart framing around the file
_ENVELOPE_BYTES = 64 * 1024
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
    return request.client.host if request.client else "unknown"


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
    """Refuses an upload by its headers, before the body is read: no size, too big, too often."""

    @app.middleware("http")
    async def guard_uploads(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        if request.method != "POST" or request.url.path != UPLOAD_PATH:
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
