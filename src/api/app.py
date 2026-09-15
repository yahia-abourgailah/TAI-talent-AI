"""HTTP entry point. Run with ``uvicorn api.app:create_app --factory``.

Dependencies are passed in or built from settings at startup, so tests use fakes and a
misconfigured deployment fails before it serves anything.
"""

import logging
import time
import uuid
from collections.abc import Awaitable, Callable, Mapping
from contextlib import AbstractContextManager

from fastapi import FastAPI, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.engine import Connection

from api.dev_routes import router as dev_router
from api.pipeline_routes import router as pipeline_router
from api.routes import router
from auth import DevIdentity, TokenVerifier
from config import AuthMode, Environment, Settings, get_settings
from config.logs import configure_logging
from db import make_engine
from infra.probes import Probe, default_probes
from pipeline.access import NotFound, NotPermitted, PipelineError, Refused

log = logging.getLogger("talent.api")

Transaction = Callable[[], AbstractContextManager[Connection]]

_STATUS: dict[type[PipelineError], int] = {NotFound: 404, NotPermitted: 403, Refused: 409}


def create_app(
    settings: Settings | None = None,
    *,
    probes: Mapping[str, Probe] | None = None,
    verifier: TokenVerifier | None = None,
    transaction: Transaction | None = None,
) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level)

    show_docs = settings.env is not Environment.PROD
    app = FastAPI(
        title="Talent Platform",
        version="0.1.0",
        docs_url="/docs" if show_docs else None,
        redoc_url=None,
        openapi_url="/openapi.json" if show_docs else None,
    )
    app.state.settings = settings
    app.state.probes = dict(probes) if probes is not None else default_probes(settings)
    # The engine connects lazily, so building the app never touches the database.
    app.state.transaction = transaction or make_engine(settings.db_dsn.get_secret_value()).begin
    if settings.auth_mode is AuthMode.DEV:
        dev_identity = DevIdentity()
        app.state.dev_identity = dev_identity
        app.state.verifier = verifier or dev_identity.verifier()
        app.include_router(dev_router)
        log.warning("sign-in uses fake dev accounts from POST /dev/token (dev only)")
    else:
        app.state.verifier = verifier or TokenVerifier(settings.oidc_issuer, settings.oidc_audience)

    @app.middleware("http")
    async def log_requests(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        request_id = (request.headers.get("x-request-id") or uuid.uuid4().hex)[:64]
        started = time.perf_counter()
        response = await call_next(request)
        response.headers["x-request-id"] = request_id
        log.info(
            "request",
            extra={
                "request_id": request_id,
                "method": request.method,
                "path": request.url.path,
                "status": response.status_code,
                "duration_ms": round((time.perf_counter() - started) * 1000, 1),
            },
        )
        return response

    @app.exception_handler(PipelineError)
    async def pipeline_refusal(request: Request, exc: Exception) -> JSONResponse:
        code = next((code for kind, code in _STATUS.items() if isinstance(exc, kind)), 409)
        return JSONResponse({"detail": str(exc)}, status_code=code)

    @app.exception_handler(RequestValidationError)
    async def invalid_request(request: Request, exc: Exception) -> JSONResponse:
        # FastAPI echoes the submitted values by default; a response never carries them.
        errors = exc.errors() if isinstance(exc, RequestValidationError) else []
        detail = [
            {"loc": list(e.get("loc", ())), "msg": e.get("msg"), "type": e.get("type")}
            for e in errors
        ]
        return JSONResponse({"detail": detail}, status_code=422)

    app.include_router(router)
    app.include_router(pipeline_router)
    return app
