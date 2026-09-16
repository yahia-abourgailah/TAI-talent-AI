"""Pipeline access without a database: roles, sign-in on every route, and what errors reveal."""

import re
from contextlib import contextmanager
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from api.app import create_app
from auth import Principal
from pipeline.access import Actor, NotPermitted, actor_from_principal, reader_from_principal

SRC = Path(__file__).resolve().parents[2] / "src"
ROUTES = (
    ("get", "/v1/reference/stages"),
    ("get", "/v1/reference/reasons"),
    ("post", "/v1/requisitions"),
    ("get", "/v1/requisitions"),
    ("get", "/v1/requisitions/req_1"),
    ("post", "/v1/requisitions/req_1/close"),
    ("post", "/v1/applications"),
    ("get", "/v1/applications"),
    ("get", "/v1/applications/app_1"),
    ("post", "/v1/applications/app_1/transitions"),
    ("get", "/v1/applications/app_1/transitions"),
    ("post", "/v1/applications/app_1/reversal"),
    ("get", "/v1/review-items"),
    ("get", "/v1/review-items/rvw_1"),
    ("post", "/v1/review-items/rvw_1/resolution"),
    ("get", "/v1/candidates"),
    ("get", "/v1/candidates/cand_1"),
    ("get", "/v1/candidates/cand_1/evaluations"),
    ("get", "/v1/evaluations/evl_1"),
    ("get", "/v1/events"),
    ("get", "/v1/reports/funnel"),
)


@contextmanager
def _no_database():
    yield None


@pytest.fixture
def client(make_settings):
    settings = make_settings(auth_mode="dev", oidc_issuer="", oidc_audience="")
    return TestClient(create_app(settings, probes={}, transaction=_no_database))


def _sign_in(client: TestClient, account: str) -> dict[str, str]:
    token = client.post("/dev/token", json={"account": account}).json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


def _principal(*roles: str) -> Principal:
    return Principal(subject="dev|someone", email=None, name=None, roles=frozenset(roles))


def test_a_recruiter_is_scoped_and_a_ta_lead_or_admin_sees_all():
    assert actor_from_principal(_principal("recruiter")) == Actor("dev|someone", sees_all=False)
    assert actor_from_principal(_principal("ta_lead")) == Actor("dev|someone", sees_all=True)
    # Only an admin sees a record locked by a withdrawal (BR-504).
    assert actor_from_principal(_principal("admin")) == Actor(
        "dev|someone", sees_all=True, is_admin=True
    )
    assert not actor_from_principal(_principal("ta_lead")).is_admin
    assert actor_from_principal(_principal("ta_lead")).scope()["sees_locked"] is False


@pytest.mark.parametrize("roles", [(), ("criteria_owner",)])
def test_other_roles_do_not_work_in_the_pipeline(roles):
    with pytest.raises(NotPermitted):
        actor_from_principal(_principal(*roles))


def test_the_criteria_owner_reads_everything_and_nobody_else_is_let_in():
    assert reader_from_principal(_principal("criteria_owner")) == Actor(
        "dev|someone", sees_all=True
    )
    assert reader_from_principal(_principal("recruiter")) == Actor("dev|someone", sees_all=False)
    with pytest.raises(NotPermitted):
        reader_from_principal(_principal())


@pytest.mark.parametrize(("method", "path"), ROUTES)
def test_every_pipeline_route_needs_sign_in(client, method, path):
    response = getattr(client, method)(path)
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthenticated"


def test_a_role_outside_the_pipeline_is_forbidden(client):
    response = client.get("/v1/applications", headers=_sign_in(client, "criteria-owner"))
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "forbidden"


def test_a_rejected_request_body_does_not_echo_what_was_sent(client):
    sent = {"requisition_id": {"name": "Fake Person Name"}, "candidate_id": -1}
    response = client.post("/v1/applications", json=sent, headers=_sign_in(client, "recruiter-a"))
    assert response.status_code == 400
    assert "Fake Person Name" not in response.text
    fields = response.json()["error"]["details"]["fields"]
    assert all(set(error) == {"loc", "msg", "type"} for error in fields)


def test_a_free_text_stage_is_refused_before_it_reaches_the_database(client):
    response = client.post(
        "/v1/applications/app_1/transitions",
        json={"from_stage": "new", "to_stage": "Moved to offer by hand!"},
        headers=_sign_in(client, "recruiter-a"),
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_request"


def test_no_code_path_moves_an_application_as_anything_but_a_person():
    """BR-405: the only move the code writes is a person's. A system proposes a review item."""
    statements = []
    for path in SRC.rglob("*.py"):
        source = path.read_text(encoding="utf-8")
        for match in re.finditer(r"INSERT INTO pipeline\.move\b", source):
            statements.append((path.name, source[match.start() : match.start() + 400]))
    assert statements, "expected at least one move insert"
    for name, statement in statements:
        values = statement.split("VALUES", 1)[1]
        assert "PERSON" in values or "'person'" in values, name
        assert "system" not in values, name
