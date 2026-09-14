"""HTTP entry point. Run with ``uvicorn api.app:create_app --factory``.

Dependencies are passed in or built from settings at startup, so tests use fakes and a
misconfigured deployment fails before it serves anything.
"""

import logging
import time
import uuid
from collections.abc import Awaitable, Callable, Mapping

from fastapi import FastAPI, Request, Response

from api.routes import router
from auth import TokenVerifier
from config import Environment, Settings, get_settings
from config.logs import configure_logging
from infra.probes import Probe, default_probes

log = logging.getLogger("talent.api")


def create_app(
    settings: Settings | None = None,
    *,
    probes: Mapping[str, Probe] | None = None,
    verifier: TokenVerifier | None = None,
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
    if settings.auth_dev_bypass:
        app.state.verifier = None
        log.warning("login bypass is on: every request runs as the local developer (dev only)")
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

    app.include_router(router)
    return app
