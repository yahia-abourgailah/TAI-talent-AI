import pytest
from fastapi.testclient import TestClient
from jwt.exceptions import PyJWKClientConnectionError

from api.app import create_app
from auth import TokenVerifier

from .conftest import AUDIENCE, ISSUER


def _ok() -> None:
    return None


@pytest.fixture
def client(make_settings, verifier):
    app = create_app(make_settings(), probes={"database": _ok, "cache": _ok}, verifier=verifier)
    return TestClient(app)


@pytest.fixture
def dev_client(make_settings):
    app = create_app(make_settings(auth_mode="dev", oidc_issuer="", oidc_audience=""), probes={})
    return TestClient(app)


def _sign_in(dev_client: TestClient, account: str) -> dict[str, str]:
    response = dev_client.post("/dev/token", json={"account": account})
    assert response.status_code == 200
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def test_health_needs_no_sign_in(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_ready_lists_every_dependency(client):
    response = client.get("/ready")
    assert response.status_code == 200
    assert response.json() == {"status": "ready", "checks": {"database": "ok", "cache": "ok"}}


def test_ready_is_503_and_does_not_leak_the_error(make_settings, verifier):
    def broken() -> None:
        raise RuntimeError("could not connect to postgresql://talent_app:hunter2@db")

    app = create_app(make_settings(), probes={"database": broken, "cache": _ok}, verifier=verifier)
    response = TestClient(app).get("/ready")

    assert response.status_code == 503
    assert response.json()["checks"] == {"database": "unavailable", "cache": "ok"}
    assert "hunter2" not in response.text


def test_me_requires_sign_in(client):
    response = client.get("/v1/me")
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"


def test_me_rejects_non_bearer_scheme(client, issue_token):
    response = client.get("/v1/me", headers={"Authorization": f"Basic {issue_token()}"})
    assert response.status_code == 401


def test_me_with_company_sign_in(client, issue_token):
    response = client.get("/v1/me", headers={"Authorization": f"Bearer {issue_token()}"})
    assert response.status_code == 200
    assert response.json() == {
        "subject": "user-123",
        "email": "recruiter@example.com",
        "name": "Test Recruiter",
        "roles": ["recruiter"],
    }


def test_me_rejects_expired_sign_in(client, issue_token):
    token = issue_token(exp=1)
    response = client.get("/v1/me", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 401
    assert (
        response.json()["error"]["message"]
        == "Your sign-in is invalid or has expired. Sign in again."
    )


def test_identity_provider_outage_is_503(make_settings, issue_token):
    def unreachable(_token):
        raise PyJWKClientConnectionError("connection refused")

    app = create_app(
        make_settings(),
        probes={},
        verifier=TokenVerifier(ISSUER, AUDIENCE, key_resolver=unreachable),
    )
    response = TestClient(app).get("/v1/me", headers={"Authorization": f"Bearer {issue_token()}"})
    assert response.status_code == 503


def test_dev_accounts_are_listed(dev_client):
    accounts = {a["account"]: a for a in dev_client.get("/dev/accounts").json()}
    assert set(accounts) == {"recruiter-a", "recruiter-b", "ta-lead", "criteria-owner", "admin"}
    assert accounts["recruiter-b"]["roles"] == ["recruiter"]


def test_dev_sign_in_goes_through_token_verification(dev_client):
    response = dev_client.get("/v1/me", headers=_sign_in(dev_client, "ta-lead"))
    assert response.status_code == 200
    assert response.json() == {
        "subject": "dev|ta-lead",
        "email": "ta-lead@dev.talent.invalid",
        "name": "Dev TA Lead",
        "roles": ["ta_lead"],
    }


def test_dev_mode_still_requires_a_token(dev_client):
    assert dev_client.get("/v1/me").status_code == 401
    forged = {"Authorization": "Bearer not-a-real-token"}
    assert dev_client.get("/v1/me", headers=forged).status_code == 401


def test_unknown_dev_account_says_which_exist(dev_client):
    response = dev_client.post("/dev/token", json={"account": "root"})
    assert response.status_code == 404
    assert "recruiter-a" in response.json()["error"]["message"]


def test_dev_sign_in_routes_do_not_exist_with_company_sign_in(client):
    assert client.get("/dev/accounts").status_code == 404
    assert client.post("/dev/token", json={"account": "admin"}).status_code == 404


def test_api_docs_hidden_in_prod(make_settings, verifier):
    app = create_app(make_settings(env="prod"), probes={}, verifier=verifier)
    assert TestClient(app).get("/docs").status_code == 404


def test_request_id_is_returned(client):
    response = client.get("/health", headers={"X-Request-ID": "abc123"})
    assert response.headers["x-request-id"] == "abc123"
