"""Pipeline access without a database: roles, sign-in on every route, and what errors reveal."""

import re
from contextlib import contextmanager
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from api.app import create_app
from auth import Principal
from pipeline.access import Actor, NotPermitted, actor_from_principal

SRC = Path(__file__).resolve().parents[2] / "src"
ROUTES = (
    ("get", "/v1/pipeline/steps"),
    ("post", "/v1/openings"),
    ("get", "/v1/openings"),
    ("get", "/v1/openings/1"),
    ("post", "/v1/openings/1/close"),
    ("post", "/v1/applications"),
    ("get", "/v1/applications"),
    ("get", "/v1/applications/1"),
    ("post", "/v1/applications/1/moves"),
    ("get", "/v1/applications/1/moves"),
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


def test_a_recruiter_is_scoped_and_a_ta_lead_sees_all():
    assert actor_from_principal(_principal("recruiter")) == Actor("dev|someone", sees_all=False)
    assert actor_from_principal(_principal("ta_lead")) == Actor("dev|someone", sees_all=True)


@pytest.mark.parametrize("roles", [(), ("criteria_owner",), ("admin",)])
def test_other_roles_do_not_work_in_the_pipeline(roles):
    with pytest.raises(NotPermitted):
        actor_from_principal(_principal(*roles))


@pytest.mark.parametrize(("method", "path"), ROUTES)
def test_every_pipeline_route_needs_sign_in(client, method, path):
    response = getattr(client, method)(path)
    assert response.status_code == 401


def test_a_role_outside_the_pipeline_is_forbidden(client):
    response = client.get("/v1/applications", headers=_sign_in(client, "criteria-owner"))
    assert response.status_code == 403


def test_a_rejected_request_body_does_not_echo_what_was_sent(client):
    sent = {"opening_id": "Fake Person Name", "candidate_id": -1}
    response = client.post("/v1/applications", json=sent, headers=_sign_in(client, "recruiter-a"))
    assert response.status_code == 422
    assert "Fake Person Name" not in response.text
    assert all(set(error) == {"loc", "msg", "type"} for error in response.json()["detail"])


def test_a_free_text_step_is_refused_before_it_reaches_the_database(client):
    response = client.post(
        "/v1/applications/1/moves",
        json={"from_step": "new", "to_step": "Moved to offer by hand!"},
        headers=_sign_in(client, "recruiter-a"),
    )
    assert response.status_code == 422


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
