import logging
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from api.deps import current_principal
from auth import Principal

log = logging.getLogger("talent.api")

router = APIRouter()


@router.get("/health", tags=["operations"])
def health() -> dict[str, str]:
    """Liveness only: the process is up. A dependency outage must not restart it."""
    return {"status": "ok"}


@router.get("/ready", tags=["operations"])
def ready(request: Request) -> JSONResponse:
    """Readiness: every dependency answers. Names the one that is down, never the error text."""
    checks: dict[str, str] = {}
    for name, probe in request.app.state.probes.items():
        try:
            probe()
        except Exception as exc:
            log.warning(
                "dependency unavailable", extra={"dependency": name, "error": type(exc).__name__}
            )
            checks[name] = "unavailable"
        else:
            checks[name] = "ok"
    is_ready = all(state == "ok" for state in checks.values())
    return JSONResponse(
        {"status": "ready" if is_ready else "not ready", "checks": checks},
        status_code=200 if is_ready else 503,
    )


@router.get("/v1/me", tags=["account"])
def me(principal: Annotated[Principal, Depends(current_principal)]) -> dict[str, Any]:
    """The signed-in user, as the platform sees them."""
    return {
        "subject": principal.subject,
        "email": principal.email,
        "name": principal.name,
        "roles": sorted(principal.roles),
    }
