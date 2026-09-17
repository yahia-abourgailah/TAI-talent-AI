"""How the platform is doing, for the people who keep it running (NFR-05).

`/health` and `/readiness` answer for a load balancer: up, or not. This is the other question — is
anything going wrong right now — and it is the same set of checks the command line and the nightly
watch run, so a recruiter lead, an engineer and a monitoring agent all read one answer.

Admins only. The checks carry counts, ages and job kinds; never a candidate.
"""

from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.engine import Connection

from api.deps import current_principal, db_connection
from auth import Principal
from config import get_settings
from ops.watch import report as build_report
from ops.watch import run_checks
from pipeline.access import NotPermitted, actor_from_principal

router = APIRouter(prefix="/v1/ops", tags=["operations"])

Signed = Annotated[Principal, Depends(current_principal)]
Db = Annotated[Connection, Depends(db_connection)]


class CheckOut(BaseModel):
    check: str
    status: str
    detail: str
    numbers: dict[str, Any] = {}


class HealthOut(BaseModel):
    status: str
    checked_at: str
    checks: list[CheckOut]
    runbook: str


@router.get("/health", response_model=HealthOut)
def operational_health(principal: Signed, conn: Db) -> HealthOut:
    """Every check the nightly watch runs: the queue, failed jobs, stuck jobs, events the CRM has
    not taken, and whether last night's backup was restored. `status` is ok, warning or critical."""
    actor = actor_from_principal(principal)
    if not actor.is_admin:
        raise NotPermitted("Only an admin reads the platform's health.")
    directory = get_settings().backup_dir
    checks = run_checks(conn, Path(directory).expanduser() if directory else None)
    return HealthOut(**build_report(checks))
