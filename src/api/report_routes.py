"""Reports (API plan section 6; BR-601, BR-109): counts from recorded history, never candidate data.

    GET /v1/reports/funnel?group_by=brand&from=2026-10-05T00:00:00Z&to=2026-10-12T00:00:00Z

The numbers come from reports.funnel: for each stage, how many applications reached it, moved on,
were rejected there (by reason) and are still there, counted only from recorded transitions, with
contactability next to volume. `from` and `to` select arrivals at a stage (from inclusive, to
exclusive) and need a time zone. A TA lead or an admin reads reports.

Next to the funnel, `review_queue` counts what is waiting for a person, per kind, and how long the
oldest item has waited (BR-407). It is today's queue, whatever `from` and `to` say.
"""

from datetime import datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.engine import Connection

from api.deps import current_principal, db_connection
from api.errors import ApiError
from api.ids import encode
from api.queue_routes import QueueSummaryOut, summary_out
from auth import Principal
from candidates import queue
from pipeline.access import NotPermitted, actor_from_principal
from reports import ReportRefused
from reports.funnel import funnel_report

router = APIRouter(prefix="/v1", tags=["reports"])

GroupBy = Literal["requisition", "brand", "recruiter", "team", "source"]
_GROUPING = {"requisition": "opening", "brand": "brand", "recruiter": "recruiter", "team": "team"}


class StageCountsOut(BaseModel):
    stage: str
    label: str
    reached: int
    moved_on: int
    rejected_here: int
    rejected_by_reason: dict[str, int]
    still_here: int
    conversion: float | None


class FunnelGroupOut(BaseModel):
    group: str
    applications: int
    candidates: int
    contactable_candidates: int
    contactability: float | None
    stages: list[StageCountsOut]


class ContactabilityOut(BaseModel):
    candidates: int
    contactable: int
    contactability: float | None


class FunnelOut(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    stage_list: str
    provisional: bool
    group_by: str | None
    date_from: str | None = Field(alias="from")
    date_to: str | None = Field(alias="to")
    groups: list[FunnelGroupOut]
    arrivals_at_stages_not_in_the_list: int
    all_candidates: ContactabilityOut
    review_queue: QueueSummaryOut


def _group_name(group_by: str | None, name: str) -> str:
    return encode("requisition", int(name)) if group_by == "requisition" else name


def _group(group_by: str | None, group: dict[str, Any]) -> FunnelGroupOut:
    return FunnelGroupOut(
        group=_group_name(group_by, group["group"]),
        applications=group["applications"],
        candidates=group["candidates"],
        contactable_candidates=group["contactable_candidates"],
        contactability=group["contactability"],
        stages=[
            StageCountsOut(
                stage=step["step"],
                label=step["label"],
                reached=step["reached"],
                moved_on=step["moved_on"],
                rejected_here=step["rejected_here"],
                rejected_by_reason=step["rejected_by_reason"],
                still_here=step["still_here"],
                conversion=step["conversion"],
            )
            for step in group["steps"]
        ],
    )


@router.get("/reports/funnel", response_model=FunnelOut, response_model_by_alias=True)
def funnel(
    principal: Annotated[Principal, Depends(current_principal)],
    conn: Annotated[Connection, Depends(db_connection)],
    group_by: Annotated[GroupBy | None, Query()] = None,
    date_from: Annotated[datetime | None, Query(alias="from")] = None,
    date_to: Annotated[datetime | None, Query(alias="to")] = None,
) -> FunnelOut:
    """Volume and conversion per stage, from recorded transitions, with contactability."""
    actor = actor_from_principal(principal)
    if not actor.sees_all:
        raise NotPermitted("Reports are for a TA lead or an admin.")
    for name, moment in (("from", date_from), ("to", date_to)):
        if moment is not None and moment.tzinfo is None:
            raise ApiError(
                400, "invalid_request", f"{name} needs a time zone, e.g. 2026-10-05T00:00:00Z."
            )
    if group_by == "source":
        raise ApiError(
            400,
            "grouping_not_available",
            "Grouping by source is not available yet: applications do not carry a channel or "
            "tracking code until the apply flow records one.",
        )
    try:
        report = funnel_report(
            conn,
            group_by=None if group_by is None else _GROUPING[group_by],
            date_from=date_from,
            date_to=date_to,
        )
    except ReportRefused as refusal:
        raise ApiError(400, "invalid_request", str(refusal)) from None
    return FunnelOut(
        stage_list=report["step_list"],
        provisional=report["provisional"],
        group_by=group_by,
        date_from=report["from"],
        date_to=report["to"],
        groups=[_group(group_by, group) for group in report["groups"]],
        arrivals_at_stages_not_in_the_list=report["arrivals_at_steps_not_in_the_list"],
        all_candidates=ContactabilityOut(**report["all_candidates"]),
        review_queue=summary_out(queue.summary(conn, actor)),
    )
