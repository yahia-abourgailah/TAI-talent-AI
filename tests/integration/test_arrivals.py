"""Week 8, BR-602: new candidates per week, by where they came from. Made-up people; all rolled
back. Counts are compared before and after, because the database may already hold others."""

from datetime import UTC, date, datetime, timedelta

from reports.arrivals import arrivals_report, week_of

from .conftest import sign_in
from .test_apply_flow import _apply, _body, job  # noqa: F401 - job is a fixture

THIS_WEEK = week_of(datetime.now(UTC))


def _this_week(conn) -> dict[str, int]:
    for week in arrivals_report(conn)["weeks"]:
        if week["week"] == THIS_WEEK:
            return {"total": week["total"], "own_page": week["own_page"], **week["by_channel"]}
    return {}


def test_arrivals_are_counted_by_channel(api_client, job):  # noqa: F811
    client, connection = api_client
    requisition, post = job
    before = _this_week(connection)

    assert (
        _apply(client, _body(requisition, client, tracking_code=post["tracking_code"])).status_code
        == 201
    )
    assert _apply(client, _body(requisition, client)).status_code == 201

    after = _this_week(connection)
    grew = {k: after.get(k, 0) - before.get(k, 0) for k in after}
    assert grew["job_post:tiktok"] == 1
    assert grew["careers_page"] == 1
    assert grew["own_page"] == 2 and grew["total"] == 2

    lead = sign_in(client, "ta-lead")
    body = client.get("/v1/reports/arrivals", headers=lead).json()
    assert THIS_WEEK in {week["week"] for week in body["weeks"]}
    recruiter = sign_in(client, "recruiter-a")
    assert client.get("/v1/reports/arrivals", headers=recruiter).status_code == 403


def test_the_sheet_gives_the_scraped_count_per_week(api_client):
    _client, connection = api_client
    monday = date.fromisoformat(THIS_WEEK)
    dates = [monday, monday + timedelta(days=2), monday - timedelta(days=7)]
    weeks = {w["week"]: w for w in arrivals_report(connection, scraped_dates=dates)["weeks"]}
    assert weeks[THIS_WEEK]["scraped_by_sheet"] == 2
    assert weeks[(monday - timedelta(days=7)).isoformat()]["scraped_by_sheet"] == 1
