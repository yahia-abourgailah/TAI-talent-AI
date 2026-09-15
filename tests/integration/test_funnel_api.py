"""GET /v1/reports/funnel (BR-601, BR-109): the funnel through the API, for a TA lead or an
admin only. Made-up candidates; everything rolls back."""

import uuid

import pytest

from .conftest import sign_in


@pytest.fixture
def walked(api_client, make_candidate):
    """One requisition, two applications: one moved to contacted, one rejected at new."""
    client, connection = api_client
    lead = sign_in(client, "ta-lead")
    requisition = client.post(
        "/v1/requisitions",
        json={
            "brand": f"Funnel Brand {uuid.uuid4().hex[:8]}",
            "department": "Sales",
            "track": "A",
            "headcount": 2,
            "team": "team-a",
            "owner_id": "dev|recruiter-a",
        },
        headers=lead,
    ).json()

    def apply(**fields) -> dict:
        candidate = make_candidate(connection, **fields)
        response = client.post(
            "/v1/applications",
            json={"requisition_id": requisition["id"], "candidate_id": f"cand_{candidate}"},
            headers=lead,
        )
        assert response.status_code == 201, response.text
        return response.json()

    def move(application: dict, to_stage: str, reason_code: str | None = None) -> None:
        response = client.post(
            f"/v1/applications/{application['id']}/transitions",
            json={"from_stage": "new", "to_stage": to_stage, "reason_code": reason_code},
            headers=lead,
        )
        assert response.status_code == 201, response.text

    move(apply(phone="made-up phone"), "contacted")
    move(apply(), "rejected", "not_reachable")
    return requisition


def _funnel(client, account: str, **params):
    return client.get("/v1/reports/funnel", params=params, headers=sign_in(client, account))


def test_a_ta_lead_reads_the_funnel_by_requisition(api_client, walked):
    client, _connection = api_client
    response = _funnel(client, "ta-lead", group_by="requisition")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["stage_list"] == "proposed-2026-09-15"
    assert set(body) >= {"from", "to", "groups", "all_candidates"}

    (group,) = [g for g in body["groups"] if g["group"] == walked["id"]]
    stages = {stage["stage"]: stage for stage in group["stages"]}
    assert "rejected" not in stages
    new = stages["new"]
    assert (new["reached"], new["moved_on"], new["rejected_here"], new["still_here"]) == (
        2,
        1,
        1,
        0,
    )
    assert new["rejected_by_reason"] == {"not_reachable": 1}
    assert new["conversion"] == 0.5
    assert (stages["contacted"]["reached"], stages["contacted"]["still_here"]) == (1, 1)
    assert (group["applications"], group["contactable_candidates"], group["contactability"]) == (
        2,
        1,
        0.5,
    )


def test_an_admin_reads_it_and_no_one_else_does(api_client, walked):
    client, _connection = api_client
    assert _funnel(client, "admin").status_code == 200
    for account in ("recruiter-a", "criteria-owner"):
        response = _funnel(client, account)
        assert response.status_code == 403, account
        assert response.json()["error"]["code"] == "forbidden"


def test_a_range_after_everything_counts_nothing(api_client, walked):
    client, _connection = api_client
    body = _funnel(client, "ta-lead", group_by="requisition", **{"from": "2999-01-01T00:00:00Z"})
    assert body.json()["groups"] == []


@pytest.mark.parametrize(
    ("params", "code"),
    [
        ({"group_by": "source"}, "grouping_not_available"),
        ({"group_by": "candidate"}, "invalid_request"),
        ({"from": "2026-10-05T00:00:00"}, "invalid_request"),
        ({"from": "2026-10-06T00:00:00Z", "to": "2026-10-05T00:00:00Z"}, "invalid_request"),
    ],
)
def test_a_report_it_cannot_give_is_a_bad_request(api_client, params, code):
    client, _connection = api_client
    response = _funnel(client, "ta-lead", **params)
    assert response.status_code == 400
    assert response.json()["error"]["code"] == code
