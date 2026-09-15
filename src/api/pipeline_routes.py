"""The pipeline API, frozen for the CRM team in week 4 (BR-401 to BR-410, API plan sections 5, 6).

Every route needs sign-in and works through pipeline.store, which scopes every query to the person
signed in (BR-408). Responses carry typed ids, stage codes and reason codes, never candidate data.
A stage changes only by POSTing a transition; the database decides whether it is allowed.
"""

from collections.abc import Mapping
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Header, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy.engine import Connection

from api import idempotency
from api.deps import current_principal, db_connection
from api.fields import Timestamp
from api.ids import decode, decode_filter, encode
from api.pages import DEFAULT_LIMIT, MAX_LIMIT, decode_cursor, next_cursor
from auth import Principal
from pipeline import store
from pipeline.access import Actor, Refused, actor_from_principal

router = APIRouter(prefix="/v1")


Signed = Annotated[Principal, Depends(current_principal)]
Db = Annotated[Connection, Depends(db_connection)]
StageCode = Annotated[str, Field(pattern=r"^[a-z][a-z_]{0,39}$")]
ReasonCode = Annotated[str, Field(pattern=r"^[a-z][a-z_]{0,59}$")]
Text = Annotated[str, Field(min_length=1, max_length=200, pattern=r"\S")]
TypedId = Annotated[str, Field(min_length=1, max_length=40)]
IdempotencyKey = Annotated[
    str | None,
    Header(
        alias=idempotency.HEADER,
        max_length=100,
        description="A UUID you generate per distinct request. A retry with it returns the "
        "first response instead of doing the work again.",
    ),
]
Limit = Annotated[int, Query(ge=1, le=MAX_LIMIT)]
Cursor = Annotated[str | None, Query(max_length=200, description="next_cursor from the last page")]


# --- Reference ----------------------------------------------------------------------------------


class StageOut(BaseModel):
    code: str
    label: str
    position: int
    outcome: str | None


class AllowedTransitionOut(BaseModel):
    from_stage: str
    to_stage: str


class StagesOut(BaseModel):
    version: str
    provisional: bool
    source: str
    loaded_at: Timestamp
    loaded_by: str
    stages: list[StageOut]
    allowed: list[AllowedTransitionOut]


class ReasonOut(BaseModel):
    code: str
    label: str


class ReasonsOut(BaseModel):
    version: str
    kind: str
    items: list[ReasonOut]


@router.get("/reference/stages", tags=["reference"])
def stages(principal: Signed, conn: Db) -> StagesOut:
    """The stage list in force: stages in order and the transitions allowed between them."""
    actor_from_principal(principal)
    listing = store.active_step_list(conn)
    return StagesOut(
        version=listing["version"],
        provisional=listing["provisional"],
        source=listing["source"],
        loaded_at=listing["loaded_at"],
        loaded_by=listing["loaded_by"],
        stages=[StageOut(**step) for step in listing["steps"]],
        allowed=[
            AllowedTransitionOut(from_stage=move["from_step"], to_stage=move["to_step"])
            for move in listing["moves"]
        ],
    )


@router.get("/reference/reasons", tags=["reference"])
def reasons(
    principal: Signed,
    conn: Db,
    kind: Annotated[Literal["rejection", "override"], Query()] = "rejection",
) -> ReasonsOut:
    """Rejection reasons from the list in force. Overrides take a written reason, so that list is
    empty for now."""
    actor_from_principal(principal)
    listing = store.active_step_list(conn)
    items = listing["rejection_reasons"] if kind == "rejection" else []
    return ReasonsOut(version=listing["version"], kind=kind, items=[ReasonOut(**r) for r in items])


# --- Requisitions -------------------------------------------------------------------------------


class RequisitionIn(BaseModel):
    brand: Text
    department: Text
    track: Literal["A", "B"]
    headcount: int = Field(gt=0, le=10_000)
    team: Text
    owner_id: Text | None = None


class CloseRequisitionIn(BaseModel):
    reason: Annotated[str, Field(min_length=1, max_length=500, pattern=r"\S")]


class RequisitionOut(BaseModel):
    id: str
    brand: str
    department: str
    track: str
    headcount: int
    status: str
    owner_id: str
    team: str
    criteria_version: str
    created_at: Timestamp
    created_by: str
    closed_at: Timestamp | None
    closed_reason: str | None
    closed_by: str | None


class RequisitionPage(BaseModel):
    items: list[RequisitionOut]
    next_cursor: str | None


def _requisition(row: Mapping[str, Any]) -> RequisitionOut:
    return RequisitionOut(
        id=encode("requisition", row["id"]),
        brand=row["brand"],
        department=row["department"],
        track=row["track"],
        headcount=row["headcount"],
        status=row["status"],
        owner_id=row["owner_recruiter"],
        team=row["team"],
        criteria_version=row["criteria_version_id"],
        created_at=row["created_at"],
        created_by=row["created_by"],
        closed_at=row["closed_at"],
        closed_reason=row["closed_reason"],
        closed_by=row["closed_by"],
    )


@router.post("/requisitions", status_code=201, response_model=RequisitionOut, tags=["requisitions"])
def create_requisition(
    body: RequisitionIn, request: Request, principal: Signed, conn: Db, key: IdempotencyKey = None
) -> JSONResponse:
    """Opens a requisition. The criteria version in force is set by the platform (BR-303)."""
    actor = actor_from_principal(principal)

    def create() -> RequisitionOut:
        row = store.create_opening(
            conn,
            actor,
            brand=body.brand,
            department=body.department,
            track=body.track,
            headcount=body.headcount,
            team=body.team,
            owner_recruiter=body.owner_id,
        )
        return _requisition(row)

    return idempotency.respond(conn, request, principal.subject, key, body, 201, create)


@router.get("/requisitions", tags=["requisitions"])
def list_requisitions(
    principal: Signed,
    conn: Db,
    limit: Limit = DEFAULT_LIMIT,
    cursor: Cursor = None,
    status_filter: Annotated[Literal["open", "closed"] | None, Query(alias="status")] = None,
    brand: Annotated[str | None, Query(max_length=200)] = None,
    track: Annotated[Literal["A", "B"] | None, Query()] = None,
    owner_id: Annotated[str | None, Query(max_length=200)] = None,
) -> RequisitionPage:
    """Requisitions in your scope, newest first."""
    rows = store.list_openings(
        conn,
        actor_from_principal(principal),
        limit=limit + 1,
        before=decode_cursor(cursor),
        status=status_filter,
        brand=brand,
        track=track,
        owner=owner_id,
    )
    return RequisitionPage(
        items=[_requisition(row) for row in rows[:limit]], next_cursor=next_cursor(rows, limit)
    )


@router.get("/requisitions/{requisition_id}", tags=["requisitions"])
def get_requisition(requisition_id: str, principal: Signed, conn: Db) -> RequisitionOut:
    actor = actor_from_principal(principal)
    return _requisition(store.get_opening(conn, actor, decode("requisition", requisition_id)))


@router.post("/requisitions/{requisition_id}/close", tags=["requisitions"])
def close_requisition(
    requisition_id: str, body: CloseRequisitionIn, principal: Signed, conn: Db
) -> RequisitionOut:
    """Closes a requisition with a reason and your name. A closed requisition is final."""
    actor = actor_from_principal(principal)
    number = decode("requisition", requisition_id)
    return _requisition(store.close_opening(conn, actor, number, body.reason))


# --- Applications and transitions ---------------------------------------------------------------


class ApplicationIn(BaseModel):
    requisition_id: TypedId
    candidate_id: TypedId
    owner_id: Text | None = None


class ApplicationOut(BaseModel):
    id: str
    requisition_id: str
    candidate_id: str
    owner_id: str
    team: str
    reopens_application_id: str | None
    current_stage: str
    outcome: str | None
    stage_since: Timestamp
    stage_changed_by: str
    transitions: int
    allowed_transitions: list[str]
    created_at: Timestamp
    created_by: str


class ApplicationPage(BaseModel):
    items: list[ApplicationOut]
    next_cursor: str | None


class TransitionIn(BaseModel):
    from_stage: StageCode
    to_stage: StageCode
    reason_code: ReasonCode | None = None


class ActorOut(BaseModel):
    id: str
    kind: str


class TransitionOut(BaseModel):
    id: str
    application_id: str
    sequence: int
    list_version: str
    from_stage: str | None
    to_stage: str
    reason_code: str | None
    actor: ActorOut
    occurred_at: Timestamp


class TransitionHistory(BaseModel):
    items: list[TransitionOut]


def _application(row: Mapping[str, Any], allowed: Mapping[str, list[str]]) -> ApplicationOut:
    reopens = row["reopens_application_id"]
    return ApplicationOut(
        id=encode("application", row["id"]),
        requisition_id=encode("requisition", row["opening_id"]),
        candidate_id=encode("candidate", row["candidate_id"]),
        owner_id=row["owner_recruiter"],
        team=row["team"],
        reopens_application_id=None if reopens is None else encode("application", reopens),
        current_stage=row["current_step"],
        outcome=row["outcome"],
        stage_since=row["step_since"],
        stage_changed_by=row["step_by"],
        transitions=row["moves"],
        allowed_transitions=[] if row["outcome"] else allowed.get(row["current_step"], []),
        created_at=row["created_at"],
        created_by=row["created_by"],
    )


def _transition(application_id: int, row: Mapping[str, Any]) -> TransitionOut:
    return TransitionOut(
        id=encode("transition", row["id"]),
        application_id=encode("application", application_id),
        sequence=row["sequence"],
        list_version=row["list_version"],
        from_stage=row["from_step"],
        to_stage=row["to_step"],
        reason_code=row["reason_code"],
        actor=ActorOut(id=row["moved_by"], kind=row["actor_kind"]),
        occurred_at=row["moved_at"],
    )


def _explained(
    conn: Connection, actor: Actor, application_id: int, body: TransitionIn, refusal: Refused
) -> Refused:
    """Adds what the caller needs to recover: the stage now, or the transitions allowed."""
    details: dict[str, Any] = {"from_stage": body.from_stage, "to_stage": body.to_stage}
    if refusal.code == "stage_changed":
        current = store.get_application(conn, actor, application_id)["current_step"]
        details["current_stage"] = current
    elif refusal.code == "transition_not_allowed":
        details["allowed"] = store.allowed_moves(conn).get(body.from_stage, [])
    else:
        return refusal
    return Refused(str(refusal), code=refusal.code, details=details)


@router.post("/applications", status_code=201, response_model=ApplicationOut, tags=["applications"])
def create_application(
    body: ApplicationIn, request: Request, principal: Signed, conn: Db, key: IdempotencyKey = None
) -> JSONResponse:
    """Attaches a candidate to a requisition. It starts at the first stage, as a transition."""
    actor = actor_from_principal(principal)
    requisition = decode("requisition", body.requisition_id)
    candidate = decode("candidate", body.candidate_id)

    def create() -> ApplicationOut:
        row = store.create_application(conn, actor, requisition, candidate, body.owner_id)
        return _application(row, store.allowed_moves(conn))

    return idempotency.respond(conn, request, principal.subject, key, body, 201, create)


@router.get("/applications", tags=["applications"])
def list_applications(
    principal: Signed,
    conn: Db,
    limit: Limit = DEFAULT_LIMIT,
    cursor: Cursor = None,
    requisition_id: Annotated[str | None, Query(max_length=40)] = None,
    stage: Annotated[str | None, Query(pattern=r"^[a-z][a-z_]{0,39}$")] = None,
    owner_id: Annotated[str | None, Query(max_length=200)] = None,
) -> ApplicationPage:
    """Applications in your scope, newest first: your own, or all of them for a TA lead."""
    rows = store.list_applications(
        conn,
        actor_from_principal(principal),
        limit=limit + 1,
        before=decode_cursor(cursor),
        opening_id=decode_filter("requisition", "requisition_id", requisition_id),
        stage=stage,
        owner=owner_id,
    )
    allowed = store.allowed_moves(conn)
    return ApplicationPage(
        items=[_application(row, allowed) for row in rows[:limit]],
        next_cursor=next_cursor(rows, limit),
    )


@router.get("/applications/{application_id}", tags=["applications"])
def get_application(application_id: str, principal: Signed, conn: Db) -> ApplicationOut:
    """One application: its stage now, its owner, and the transitions allowed from here."""
    actor = actor_from_principal(principal)
    row = store.get_application(conn, actor, decode("application", application_id))
    return _application(row, store.allowed_moves(conn))


@router.post(
    "/applications/{application_id}/transitions",
    status_code=201,
    response_model=TransitionOut,
    tags=["applications"],
)
def create_transition(
    application_id: str,
    body: TransitionIn,
    request: Request,
    principal: Signed,
    conn: Db,
    key: IdempotencyKey = None,
) -> JSONResponse:
    """Moves an application. `from_stage` must be its stage now; a rejection needs a listed
    reason (BR-404); you are recorded as the person who moved it (BR-403)."""
    actor = actor_from_principal(principal)
    number = decode("application", application_id)

    def move() -> TransitionOut:
        try:
            row = store.move_application(
                conn,
                actor,
                number,
                from_step=body.from_stage,
                to_step=body.to_stage,
                reason_code=body.reason_code,
            )
        except Refused as refusal:
            raise _explained(conn, actor, number, body, refusal) from None
        return _transition(number, row)

    return idempotency.respond(conn, request, principal.subject, key, body, 201, move)


@router.get("/applications/{application_id}/transitions", tags=["applications"])
def transition_history(application_id: str, principal: Signed, conn: Db) -> TransitionHistory:
    """Every transition, oldest first: from, to, who and when."""
    actor = actor_from_principal(principal)
    number = decode("application", application_id)
    rows = store.move_history(conn, actor, number)
    return TransitionHistory(items=[_transition(number, row) for row in rows])
