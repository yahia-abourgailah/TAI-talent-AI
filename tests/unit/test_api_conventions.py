"""API conventions without a database: the error body, typed ids, cursors, idempotency keys."""

from contextlib import contextmanager

import pytest
from fastapi.testclient import TestClient

from api.app import create_app
from api.errors import ApiError
from api.ids import decode, decode_filter, encode
from api.pages import decode_cursor, encode_cursor, next_cursor
from pipeline.access import NotFound

REQUISITION = {"brand": "B", "department": "D", "track": "A", "headcount": 1, "team": "t"}


@contextmanager
def _no_database():
    yield None


@pytest.fixture
def app(make_settings):
    settings = make_settings(auth_mode="dev", oidc_issuer="", oidc_audience="")
    return create_app(settings, probes={}, transaction=_no_database)


@pytest.fixture
def client(app):
    return TestClient(app, raise_server_exceptions=False)


def _sign_in(client: TestClient, account: str) -> dict[str, str]:
    token = client.post("/dev/token", json={"account": account}).json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


def test_ids_are_typed_and_round_trip():
    assert encode("application", 42) == "app_42"
    assert decode("application", "app_42") == 42
    assert decode_filter("requisition", "requisition_id", None) is None


@pytest.mark.parametrize(
    "value",
    ["42", "req_42", "app_", "app_0", "app_042", "app_-1", "app_1234567890123456789", "APP_42"],
)
def test_a_malformed_id_is_simply_not_found(value):
    with pytest.raises(NotFound, match="Application not found"):
        decode("application", value)


def test_a_malformed_id_in_a_filter_is_a_bad_request():
    with pytest.raises(ApiError) as caught:
        decode_filter("requisition", "requisition_id", "app_1")
    assert (caught.value.status, caught.value.code) == (400, "invalid_request")


def test_cursors_round_trip_and_bad_ones_are_refused():
    assert decode_cursor(encode_cursor(981)) == 981
    assert decode_cursor(None) is None
    bad = ("", "abc", "nope", "e30", encode_cursor(0), "eyJiZWZvcmUiOiJ4In0")
    for cursor in bad:
        with pytest.raises(ApiError) as caught:
            decode_cursor(cursor)
        assert caught.value.code == "invalid_cursor"


def test_next_cursor_only_when_there_is_another_page():
    rows = [{"id": 9}, {"id": 7}, {"id": 4}]
    assert next_cursor(rows, 3) is None
    assert decode_cursor(next_cursor(rows, 2)) == 7


def test_an_error_has_one_body_carrying_the_request_id(client):
    response = client.get("/v1/me", headers={"X-Request-ID": "trace-123"})
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"
    assert response.headers["x-request-id"] == "trace-123"
    assert response.json() == {
        "error": {
            "code": "unauthenticated",
            "message": "Sign in with your company account.",
            "request_id": "trace-123",
        }
    }


def test_an_unknown_route_uses_the_same_body(client):
    error = client.get("/v1/nothing-here").json()["error"]
    assert error["code"] == "not_found"
    assert error["request_id"]


def test_an_invalid_body_names_fields_never_values(client):
    sent = {"requisition_id": ["Fake Person Name"], "candidate_id": 7}
    response = client.post("/v1/applications", json=sent, headers=_sign_in(client, "recruiter-a"))
    assert response.status_code == 400
    error = response.json()["error"]
    assert error["code"] == "invalid_request"
    assert error["details"]["fields"]
    assert all(set(field) == {"loc", "msg", "type"} for field in error["details"]["fields"])
    assert "Fake Person Name" not in response.text


def test_an_unexpected_failure_says_nothing_about_it(app):
    def boom() -> None:
        raise RuntimeError("Fake Person Name")

    app.add_api_route("/boom", boom)
    response = TestClient(app, raise_server_exceptions=False).get("/boom")
    assert response.status_code == 500
    assert response.json()["error"]["code"] == "internal_error"
    assert response.json()["error"]["request_id"]
    assert "Fake Person" not in response.text


def test_a_malformed_idempotency_key_is_refused_before_anything_runs(client):
    headers = {**_sign_in(client, "recruiter-a"), "Idempotency-Key": "not-a-uuid"}
    response = client.post("/v1/requisitions", json=REQUISITION, headers=headers)
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_idempotency_key"


def test_a_malformed_id_is_not_found_before_the_database_is_asked(client):
    response = client.get("/v1/applications/app_x", headers=_sign_in(client, "recruiter-a"))
    assert response.status_code == 404
    assert response.json()["error"] == {
        "code": "not_found",
        "message": "Application not found.",
        "request_id": response.headers["x-request-id"],
    }
