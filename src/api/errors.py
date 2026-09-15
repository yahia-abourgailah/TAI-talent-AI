"""Every response that is not a success has one body (API plan section 5).

    {"error": {"code": "transition_not_allowed", "message": "...", "request_id": "...",
               "details": {...}}}

`code` is stable and meant for the caller's code; `message` is for people and may change;
`details` is optional and depends on `code`. No part of it ever carries a value someone submitted
or a stored candidate value: validation errors name fields, never their contents, and an
unexpected failure says only that it happened.
"""

import logging
from collections.abc import Mapping
from http import HTTPStatus
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.openapi.utils import get_openapi
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException

from pipeline.access import PipelineError

log = logging.getLogger("talent.api")

_CODE_FOR_STATUS: dict[int, str] = {
    400: "invalid_request",
    401: "unauthenticated",
    403: "forbidden",
    404: "not_found",
    405: "method_not_allowed",
    409: "conflict",
    413: "payload_too_large",
    415: "unsupported_media_type",
    429: "rate_limited",
    503: "unavailable",
}


class ApiError(Exception):
    """A refusal raised by the API layer itself. The message is safe to show."""

    def __init__(
        self, status: int, code: str, message: str, details: Mapping[str, Any] | None = None
    ) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.details = dict(details) if details else None


def request_id(request: Request) -> str:
    return str(getattr(request.state, "request_id", ""))


def error_response(
    request: Request,
    status: int,
    code: str,
    message: str,
    details: Mapping[str, Any] | None = None,
    headers: Mapping[str, str] | None = None,
) -> JSONResponse:
    error: dict[str, Any] = {"code": code, "message": message, "request_id": request_id(request)}
    if details:
        error["details"] = dict(details)
    response = JSONResponse(
        {"error": error}, status_code=status, headers=dict(headers) if headers else None
    )
    response.headers["x-request-id"] = request_id(request)
    return response


def install(app: FastAPI) -> None:
    """Routes every refusal and every failure through the one body."""

    @app.exception_handler(ApiError)
    async def api_error(request: Request, exc: Exception) -> JSONResponse:
        if not isinstance(exc, ApiError):
            raise exc
        return error_response(request, exc.status, exc.code, exc.message, exc.details)

    @app.exception_handler(PipelineError)
    async def pipeline_refusal(request: Request, exc: Exception) -> JSONResponse:
        if not isinstance(exc, PipelineError):
            raise exc
        return error_response(request, exc.status, exc.code, str(exc), exc.details)

    @app.exception_handler(HTTPException)
    async def http_error(request: Request, exc: Exception) -> JSONResponse:
        if not isinstance(exc, HTTPException):
            raise exc
        message = exc.detail if isinstance(exc.detail, str) else HTTPStatus(exc.status_code).phrase
        code = _CODE_FOR_STATUS.get(exc.status_code, "error")
        return error_response(request, exc.status_code, code, message, headers=exc.headers)

    @app.exception_handler(RequestValidationError)
    async def invalid_request(request: Request, exc: Exception) -> JSONResponse:
        # FastAPI echoes the submitted values by default; this body names the fields only.
        found = exc.errors() if isinstance(exc, RequestValidationError) else []
        fields = [
            {"loc": list(e.get("loc", ())), "msg": e.get("msg"), "type": e.get("type")}
            for e in found
        ]
        return error_response(
            request,
            400,
            "invalid_request",
            "The request is not valid. details.fields names each problem.",
            {"fields": fields},
        )

    @app.exception_handler(Exception)
    async def unexpected(request: Request, exc: Exception) -> JSONResponse:
        log.error(
            "unexpected failure",
            extra={"error": type(exc).__name__, "request_id": request_id(request)},
        )
        return error_response(
            request,
            500,
            "internal_error",
            "Something went wrong on our side. Quote the request ID when you report it.",
        )


ERROR_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["error"],
    "properties": {
        "error": {
            "type": "object",
            "required": ["code", "message", "request_id"],
            "properties": {
                "code": {"type": "string", "description": "Stable, for your code"},
                "message": {"type": "string", "description": "For people; may change"},
                "request_id": {"type": "string"},
                "details": {"type": "object", "description": "Depends on code"},
            },
        }
    },
}


def document(app: FastAPI) -> None:
    """The OpenAPI document shows the error body every route really returns, instead of
    FastAPI's default 422 validation schema."""

    def openapi() -> dict[str, Any]:
        if app.openapi_schema:
            return app.openapi_schema
        schema = get_openapi(title=app.title, version=app.version, routes=app.routes)
        schemas = schema.setdefault("components", {}).setdefault("schemas", {})
        schemas.pop("HTTPValidationError", None)
        schemas.pop("ValidationError", None)
        schemas["Error"] = ERROR_SCHEMA
        error = {
            "description": "Error: see the code (API plan section 5)",
            "content": {"application/json": {"schema": {"$ref": "#/components/schemas/Error"}}},
        }
        for item in schema.get("paths", {}).values():
            for operation in item.values():
                if isinstance(operation, dict) and "responses" in operation:
                    operation["responses"].pop("422", None)
                    operation["responses"].setdefault("default", error)
        app.openapi_schema = schema
        return schema

    app.openapi = openapi  # type: ignore[method-assign]
