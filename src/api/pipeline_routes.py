"""The first pipeline API (BR-410, frozen in week 4).

Every route needs sign-in and works through pipeline.store, which scopes every query to the person
signed in (BR-408). Responses carry ids, step codes and reason codes, never candidate data.
"""

from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query, status
from pydantic import BaseModel, Field
from sqlalchemy.engine import Connection

from api.deps import current_principal, db_connection
from auth import Principal
from pipeline import store
from pipeline.access import Actor, actor_from_principal

router = APIRouter(prefix="/v1", tags=["pipeline"])

Signed = Annotated[Principal, Depends(current_principal)]
Db = Annotated[Connection, Depends(db_connection)]
StepCode = Annotated[str, Field(pattern=r"^[a-z][a-z_]{0,39}$")]
Text = Annotated[str, Field(min_length=1, max_length=200, pattern=r"\S")]


def _actor(principal: Principal) -> Actor:
    return actor_from_principal(principal)


class OpeningIn(BaseModel):
    brand: Text
    department: Text
    track: Literal["A", "B"]
    headcount: int = Field(gt=0, le=10_000)
    team: Text
    owner_recruiter: Text | None = None


class CloseOpeningIn(BaseModel):
    reason: Annotated[str, Field(min_length=1, max_length=500, pattern=r"\S")]


class OpeningOut(BaseModel):
    id: int
    brand: str
    department: str
    track: str
    headcount: int
    status: str
    owner_recruiter: str
    team: str
    criteria_version_id: str
    created_at: datetime
    created_by: str
    closed_at: datetime | None
    closed_reason: str | None
    closed_by: str | None


class ApplicationIn(BaseModel):
    opening_id: int = Field(gt=0)
    candidate_id: int = Field(gt=0)
    owner_recruiter: Text | None = None


class ApplicationOut(BaseModel):
    id: int
    opening_id: int
    candidate_id: int
    owner_recruiter: str
    team: str
    reopens_application_id: int | None
    created_at: datetime
    created_by: str
    current_step: str
    moves: int
    step_since: datetime
    step_by: str
    outcome: str | None


class MoveIn(BaseModel):
    from_step: StepCode
    to_step: StepCode
    reason_code: Annotated[str, Field(pattern=r"^[a-z][a-z_]{0,59}$")] | None = None


class MoveOut(BaseModel):
    id: int
    sequence: int
    list_version: str
    from_step: str | None
    to_step: str
    reason_code: str | None
    actor_kind: str
    moved_by: str
    moved_at: datetime


class StepOut(BaseModel):
    code: str
    label: str
    position: int
    outcome: str | None


class AllowedMoveOut(BaseModel):
    from_step: str
    to_step: str


class ReasonOut(BaseModel):
    code: str
    label: str


class StepListOut(BaseModel):
    version: str
    provisional: bool
    source: str
    loaded_at: datetime
    loaded_by: str
    steps: list[StepOut]
    moves: list[AllowedMoveOut]
    rejection_reasons: list[ReasonOut]


Limit = Annotated[int, Query(ge=1, le=200)]
Offset = Annotated[int, Query(ge=0)]


@router.get("/pipeline/steps")
def step_list(principal: Signed, conn: Db) -> StepListOut:
    """The step list in force: steps in order, allowed moves and rejection reasons."""
    _actor(principal)
    return StepListOut.model_validate(store.active_step_list(conn))


@router.post("/openings", status_code=status.HTTP_201_CREATED)
def create_opening(body: OpeningIn, principal: Signed, conn: Db) -> OpeningOut:
    row = store.create_opening(conn, _actor(principal), **body.model_dump())
    return OpeningOut.model_validate(row)


@router.get("/openings")
def list_openings(
    principal: Signed, conn: Db, limit: Limit = 50, offset: Offset = 0
) -> list[OpeningOut]:
    rows = store.list_openings(conn, _actor(principal), limit, offset)
    return [OpeningOut.model_validate(row) for row in rows]


@router.get("/openings/{opening_id}")
def get_opening(opening_id: int, principal: Signed, conn: Db) -> OpeningOut:
    return OpeningOut.model_validate(store.get_opening(conn, _actor(principal), opening_id))


@router.post("/openings/{opening_id}/close")
def close_opening(opening_id: int, body: CloseOpeningIn, principal: Signed, conn: Db) -> OpeningOut:
    """Archives an opening with a reason and the person closing it. A closed opening is final."""
    row = store.close_opening(conn, _actor(principal), opening_id, body.reason)
    return OpeningOut.model_validate(row)


@router.post("/applications", status_code=status.HTTP_201_CREATED)
def create_application(body: ApplicationIn, principal: Signed, conn: Db) -> ApplicationOut:
    """Starts an application at the first step of the list in force, as a recorded move."""
    row = store.create_application(
        conn, _actor(principal), body.opening_id, body.candidate_id, body.owner_recruiter
    )
    return ApplicationOut.model_validate(row)


@router.get("/applications")
def list_applications(
    principal: Signed,
    conn: Db,
    limit: Limit = 50,
    offset: Offset = 0,
    opening_id: Annotated[int | None, Query(gt=0)] = None,
) -> list[ApplicationOut]:
    """Applications you own; all of them for a TA lead."""
    rows = store.list_applications(conn, _actor(principal), limit, offset, opening_id)
    return [ApplicationOut.model_validate(row) for row in rows]


@router.get("/applications/{application_id}")
def get_application(application_id: int, principal: Signed, conn: Db) -> ApplicationOut:
    row = store.get_application(conn, _actor(principal), application_id)
    return ApplicationOut.model_validate(row)


@router.post("/applications/{application_id}/moves", status_code=status.HTTP_201_CREATED)
def move_application(application_id: int, body: MoveIn, principal: Signed, conn: Db) -> MoveOut:
    """Moves an application one step. `from_step` must be its current step."""
    row = store.move_application(
        conn,
        _actor(principal),
        application_id,
        from_step=body.from_step,
        to_step=body.to_step,
        reason_code=body.reason_code,
    )
    return MoveOut.model_validate(row)


@router.get("/applications/{application_id}/moves")
def move_history(application_id: int, principal: Signed, conn: Db) -> list[MoveOut]:
    """Every move, oldest first: from, to, who and when."""
    rows = store.move_history(conn, _actor(principal), application_id)
    return [MoveOut.model_validate(row) for row in rows]
