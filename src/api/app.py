"""HTTP entry point. Run with ``uvicorn api.app:create_app --factory``.

Dependencies are passed in or built from settings at startup, so tests use fakes and a
misconfigured deployment fails before it serves anything.
"""

import logging
import time
import uuid
from collections.abc import Awaitable, Callable, Mapping
from contextlib import AbstractContextManager
from pathlib import Path

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from sqlalchemy.engine import Connection

from api import errors
from api.candidate_review_routes import router as candidate_review_router
from api.candidate_routes import router as candidate_router
from api.dev_routes import router as dev_router
from api.event_routes import router as event_router
from api.limits import RateLimiter
from api.ops_routes import router as ops_router
from api.pipeline_routes import router as pipeline_router
from api.public_routes import install_upload_guard
from api.public_routes import router as public_router
from api.queue_routes import router as queue_router
from api.report_routes import router as report_router
from api.review_routes import router as review_router
from api.routes import router
from api.withdrawal_routes import router as withdrawal_router
from auth import DevIdentity, TokenVerifier
from config import AuthMode, Environment, Settings, get_settings
from config.logs import configure_logging
from db import make_engine
from importer.blobs import BlobStore
from infra.probes import Probe, default_probes

log = logging.getLogger("talent.api")

Transaction = Callable[[], AbstractContextManager[Connection]]


def create_app(
    settings: Settings | None = None,
    *,
    probes: Mapping[str, Probe] | None = None,
    verifier: TokenVerifier | None = None,
    transaction: Transaction | None = None,
    blobs: BlobStore | None = None,
    rate_limiter: RateLimiter | None = None,
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
    # Object storage is reached on first use (api.deps.blob_store).
    app.state.blobs = blobs
    app.state.rate_limiter = rate_limiter or RateLimiter()
    if settings.auth_mode is AuthMode.DEV:
        dev_identity = DevIdentity()
        app.state.dev_identity = dev_identity
        app.state.verifier = verifier or dev_identity.verifier()
        app.include_router(dev_router)
        log.warning("sign-in uses fake dev accounts from POST /dev/token (dev only)")
    else:
        app.state.verifier = verifier or TokenVerifier(settings.oidc_issuer, settings.oidc_audience)

    # Added first, so it runs inside log_requests and its refusals carry the request id.
    install_upload_guard(app)

    @app.middleware("http")
    async def log_requests(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        request_id = (request.headers.get("x-request-id") or uuid.uuid4().hex)[:64]
        request.state.request_id = request_id  # error bodies quote it
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

    origins = settings.cors_origin_list
    if origins:
        # Only the pages we name may call us from a browser: the careers site and the dashboard
        # (D-WEB-2). No cookies are involved — callers send a bearer token or an upload token — so
        # credentials stay off, which also keeps a wildcard from ever being usable against us.
        app.add_middleware(
            CORSMiddleware,
            allow_origins=origins,
            allow_credentials=False,
            allow_methods=["GET", "POST", "OPTIONS"],
            allow_headers=["Authorization", "Content-Type", "Idempotency-Key", "X-Upload-Token"],
            max_age=600,
        )

    errors.install(app)
    errors.document(app)

    app.include_router(router)
    app.include_router(pipeline_router)
    app.include_router(event_router)
    app.include_router(review_router)
    app.include_router(candidate_review_router)
    app.include_router(queue_router)
    app.include_router(candidate_router)
    app.include_router(report_router)
    app.include_router(withdrawal_router)
    app.include_router(ops_router)
    app.include_router(public_router)

    # A console for trying the platform by hand, and an example careers page. Development only:
    # the dashboard belongs to the CRM team and the careers page to the website team, and neither
    # of these has been through their review. Both are plain clients of the API above.
    # Next to the source in a checkout, next to the working directory in the image.
    console = next(
        (
            path
            for path in (Path(__file__).resolve().parents[2] / "web", Path.cwd() / "web")
            if (path / "careers").is_dir()
        ),
        None,
    )
    if settings.env is Environment.DEV and console is not None:
        app.mount("/careers", StaticFiles(directory=console / "careers", html=True), name="careers")
        app.mount("/app", StaticFiles(directory=console, html=True), name="console")
        log.info("development console mounted at /app and /careers")

    return app
