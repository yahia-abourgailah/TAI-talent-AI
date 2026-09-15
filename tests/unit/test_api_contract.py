"""The frozen /v1 contract: the snapshot matches the code, and breaking changes are caught."""

import copy
import json

import pytest

from api.contract import SNAPSHOT, breaking_changes, current_spec


@pytest.fixture(scope="module")
def spec():
    return current_spec()


def test_the_code_matches_the_recorded_contract(spec):
    recorded = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
    assert breaking_changes(recorded, spec) == [], "breaking change to /v1: make it additive"
    assert recorded == spec, "additive change not recorded: run python -m api.contract --write"


def test_the_contract_leaves_out_dev_routes_and_shows_the_real_error_body(spec):
    assert not [path for path in spec["paths"] if path.startswith("/dev")]
    assert "HTTPValidationError" not in spec["components"]["schemas"]
    transitions = spec["paths"]["/v1/applications/{application_id}/transitions"]["post"]
    assert "422" not in transitions["responses"]
    assert transitions["responses"]["default"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/Error"
    }


def _ref(name: str) -> dict:
    return {"$ref": f"#/components/schemas/{name}"}


def _base() -> dict:
    return {
        "paths": {
            "/v1/things": {
                "get": {
                    "parameters": [{"in": "query", "name": "limit", "required": False}],
                    "responses": {
                        "200": {"content": {"application/json": {"schema": _ref("ThingOut")}}}
                    },
                },
                "post": {
                    "requestBody": {"content": {"application/json": {"schema": _ref("ThingIn")}}},
                    "responses": {
                        "201": {"content": {"application/json": {"schema": _ref("ThingOut")}}}
                    },
                },
            }
        },
        "components": {
            "schemas": {
                "ThingIn": {"properties": {"name": {"type": "string"}}, "required": ["name"]},
                "ThingOut": {
                    "properties": {"id": {"type": "string"}, "owner": _ref("OwnerOut")},
                    "required": ["id"],
                },
                "OwnerOut": {"properties": {"id": {"type": "string"}}},
            }
        },
    }


def test_additive_changes_are_not_breaking():
    old, new = _base(), _base()
    new["paths"]["/v1/other"] = {"get": {"responses": {}}}
    new["paths"]["/v1/things"]["get"]["parameters"].append({"in": "query", "name": "brand"})
    new["components"]["schemas"]["ThingIn"]["properties"]["note"] = {"type": "string"}
    new["components"]["schemas"]["ThingOut"]["properties"]["created_at"] = {"type": "string"}
    new["components"]["schemas"]["ThingOut"]["properties"]["id"]["description"] = "Typed id"
    assert breaking_changes(old, new) == []


@pytest.mark.parametrize(
    ("change", "expected"),
    [
        (lambda s: s["paths"]["/v1/things"].pop("post"), "POST /v1/things: endpoint removed"),
        (
            lambda s: s["paths"]["/v1/things"]["get"]["parameters"].clear(),
            "GET /v1/things: query parameter limit removed",
        ),
        (
            lambda s: s["paths"]["/v1/things"]["get"]["parameters"][0].update(required=True),
            "GET /v1/things: query parameter limit is now required",
        ),
        (
            lambda s: s["components"]["schemas"]["ThingOut"]["properties"].pop("id"),
            "ThingOut.id: removed from a response",
        ),
        (
            lambda s: s["components"]["schemas"]["OwnerOut"]["properties"]["id"].update(
                type="integer"
            ),
            "OwnerOut.id: type changed in a response",
        ),
        (
            lambda s: s["components"]["schemas"]["ThingIn"].update(required=["name", "note"]),
            "ThingIn.note: now required in a request",
        ),
    ],
)
def test_breaking_changes_are_named(change, expected):
    old, new = _base(), copy.deepcopy(_base())
    change(new)
    assert expected in breaking_changes(old, new)
