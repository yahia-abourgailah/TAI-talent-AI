"""The pipeline API, frozen for the CRM team in week 4 (BR-401 to BR-410, API plan sections 5, 6).

Every route needs sign-in and works through pipeline.store, which scopes every query to the person
signed in (BR-408). Responses carry typed ids, stage codes and reason codes, never candidate data.
A stage changes only by POSTing a transition; the database decides whether it is allowed.
"""

from collections.abc import Mapping
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Header, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, model_validator
from sqlalchemy.engine import Connection

from api import idempotency
from api.deps import current_principal, db_connection
from api.fields import Timestamp
from api.ids import decode, decode_filter, encode
from api.pages import DEFAULT_LIMIT, MAX_LIMIT, decode_cursor, next_cursor
from auth import Principal
from intake import job_posts
from pipeline import store
from pipeline.access import Actor, NotPermitted, Refused, actor_from_principal

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


class SkillIn(BaseModel):
    """One thing the job asks for, and how much of it.

    The four levels are the ones the CV service grades against; a level it does not know would
    give a percentage that means nothing, so nothing else is accepted (migration 0020).
    """

    skill: Annotated[str, Field(min_length=1, max_length=80, pattern=r"\S")]
    level: Literal["beginner", "intermediate", "advanced", "expert"]
    category: Annotated[str, Field(min_length=1, max_length=40, pattern=r"\S")] | None = None


class SkillOut(BaseModel):
    skill: str
    level: str
    category: str | None = None


class RequisitionIn(BaseModel):
    brand: Text
    department: Text
    track: Literal["A", "B"]
    headcount: int = Field(gt=0, le=10_000)
    team: Text
    owner_id: Text | None = None
    # A TA lead or an admin may name the version, e.g. from TA's open-jobs file. Otherwise the
    # version in force is used (BR-303).
    criteria_version: Annotated[str, Field(pattern=r"^[0-9A-Za-z._-]{1,64}$")] | None = None
    # What a careers page may show. A public requisition needs a title (BR-401, week 6).
    title: Text | None = None
    location: Text | None = None
    public: bool = False
    # How a candidate for this job is judged. `sales` is the criteria version, as always. `other`
    # is judged against the skills it lists, matched against the candidate's own CV file, for a
    # person to act on. A job of that kind says what it asks for twice: in prose for a person to
    # read, and as skills for the match (BR-305).
    job_type: Literal["sales", "other"] = "sales"
    description: Annotated[str, Field(min_length=40, max_length=8000)] | None = None
    requirements: Annotated[list[SkillIn], Field(max_length=40)] | None = None

    @model_validator(mode="after")
    def _other_jobs_say_what_they_ask_for(self) -> "RequisitionIn":
        if self.job_type == "other" and not (self.description or "").strip():
            raise ValueError(
                "a job that is not sales needs a description: it is what a person reads"
            )
        if self.job_type == "other" and not self.requirements:
            raise ValueError(
                "a job that is not sales needs the skills it asks for: they are what each CV is "
                "matched against"
            )
        if self.job_type != "other" and self.requirements:
            raise ValueError(
                "a sales job is scored by the criteria version, which has its own rules: it does "
                "not take a list of skills"
            )
        seen = {skill.skill.strip().lower() for skill in self.requirements or []}
        if len(seen) != len(self.requirements or []):
            raise ValueError("the same skill is asked for twice")
        return self


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
    title: str | None
    location: str | None
    public: bool
    job_type: str
    description: str | None
    requirements: list[SkillOut] | None = None


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
        title=row["title"],
        location=row["location"],
        public=row["public"],
        job_type=row["job_type"],
        description=row["description"],
        requirements=(
            None
            if row["requirements"] is None
            else [SkillOut(**skill) for skill in row["requirements"]]
        ),
    )


@router.post("/requisitions", status_code=201, response_model=RequisitionOut, tags=["requisitions"])
def create_requisition(
    body: RequisitionIn, request: Request, principal: Signed, conn: Db, key: IdempotencyKey = None
) -> JSONResponse:
    """Opens a requisition. The criteria version in force is set by the platform (BR-303)."""
    actor = actor_from_principal(principal)
    if body.criteria_version is not None and not actor.sees_all:
        raise NotPermitted("Only a TA lead or an admin chooses the criteria version.")

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
            criteria_version_id=body.criteria_version,
            title=body.title,
            location=body.location,
            public=body.public,
            job_type=body.job_type,
            description=body.description,
            requirements=(
                None
                if body.requirements is None
                else [
                    {
                        "skill": skill.skill.strip(),
                        "level": skill.level,
                        **({"category": skill.category.strip()} if skill.category else {}),
                    }
                    for skill in body.requirements
                ]
            ),
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
    archived: Annotated[
        bool | None,
        Query(
            description="Follows the candidate: false leaves out archived candidates' applications"
        ),
    ] = None,
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
        archived=archived,
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


class ReversalIn(BaseModel):
    reason: Annotated[str, Field(min_length=1, max_length=500, pattern=r"\S")]


@router.post(
    "/applications/{application_id}/reversal",
    status_code=201,
    response_model=ApplicationOut,
    tags=["applications"],
)
def reverse_rejection(
    application_id: str,
    body: ReversalIn,
    request: Request,
    principal: Signed,
    conn: Db,
    key: IdempotencyKey = None,
) -> JSONResponse:
    """Reverses a rejection, with a reason and your name (BR-406). The rejected application stays
    rejected; a new application, linked to it, starts again at the first stage."""
    actor = actor_from_principal(principal)
    number = decode("application", application_id)

    def reverse() -> ApplicationOut:
        row = store.reverse_rejection(conn, actor, number, body.reason)
        return _application(row, store.allowed_moves(conn))

    return idempotency.respond(conn, request, principal.subject, key, body, 201, reverse)


# --- Job posts and their tracking codes (BR-602) --------------------------------------------------


class JobPostIn(BaseModel):
    channel: Annotated[str, Field(pattern=r"^[a-z][a-z_]{1,29}$")]
    label: Annotated[str, Field(min_length=1, max_length=200, pattern=r"\S")] | None = None
    # Leave out to be given one.
    code: Annotated[str, Field(pattern=r"^[a-z0-9][a-z0-9-]{3,39}$")] | None = None


class JobPostOut(BaseModel):
    tracking_code: str
    requisition_id: str
    channel: str
    label: str | None
    created_at: Timestamp
    created_by: str


class JobPostList(BaseModel):
    items: list[JobPostOut]


def _job_post(row: Mapping[str, Any]) -> JobPostOut:
    return JobPostOut(
        tracking_code=row["code"],
        requisition_id=encode("requisition", row["opening_id"]),
        channel=row["channel"],
        label=row["label"],
        created_at=row["created_at"],
        created_by=row["created_by"],
    )


@router.post(
    "/requisitions/{requisition_id}/job-posts",
    status_code=201,
    response_model=JobPostOut,
    tags=["requisitions"],
)
def create_job_post(
    requisition_id: str,
    body: JobPostIn,
    request: Request,
    principal: Signed,
    conn: Db,
    key: IdempotencyKey = None,
) -> JSONResponse:
    """A tracking code for one job post, so we can see which post brings good candidates. Put the
    code in the link you publish; the apply page sends it back."""
    actor = actor_from_principal(principal)
    number = decode("requisition", requisition_id)

    def create() -> JobPostOut:
        return _job_post(
            job_posts.issue(
                conn, actor, number, channel=body.channel, label=body.label, code=body.code
            )
        )

    return idempotency.respond(conn, request, principal.subject, key, body, 201, create)


@router.get("/requisitions/{requisition_id}/job-posts", tags=["requisitions"])
def list_job_posts(requisition_id: str, principal: Signed, conn: Db) -> JobPostList:
    """Every job post of a requisition, oldest first."""
    actor = actor_from_principal(principal)
    rows = job_posts.list_for_opening(conn, actor, decode("requisition", requisition_id))
    return JobPostList(items=[_job_post(row) for row in rows])
