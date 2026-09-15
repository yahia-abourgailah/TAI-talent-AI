"""The frozen /v1 contract (API plan section 1): what the CRM and website teams build against.

    python -m api.contract --check   # exit 1 on a breaking change, or an additive one not recorded
    python -m api.contract --write   # record an additive change in docs/api/openapi-v1.json

Breaking, and refused: an endpoint removed; a parameter removed, or newly required; a request field
newly required; a response field removed, or its type changed (including becoming nullable).
Additive, and allowed once recorded: new endpoints, new optional parameters and request fields,
new response fields. The dev-only /dev routes are not part of the contract.

Checks compare schemas by name, so renaming a response model hides its fields from the check;
keep model names stable.
"""

import argparse
import json
import sys
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any, cast

from api.app import create_app
from config import Settings

SNAPSHOT = Path(__file__).resolve().parents[2] / "docs" / "api" / "openapi-v1.json"
_METHODS = frozenset({"get", "post", "put", "patch", "delete"})
_NOISE = frozenset({"title", "description", "examples", "example", "default"})
_SCHEMA_REF = "#/components/schemas/"


@contextmanager
def _no_database() -> Iterator[None]:
    yield None


def current_spec() -> dict[str, Any]:
    """The contract as the code defines it now, without the dev routes or their schemas."""
    settings = Settings(
        _env_file=None,
        env="dev",
        auth_mode="dev",
        db_dsn="postgresql+psycopg://contract:contract@localhost:1/contract",
        redis_url="redis://localhost:1/0",
        blob_endpoint="http://localhost:1",
        blob_access_key="contract",
        blob_secret_key="contract",
    )
    app = create_app(settings, probes={}, transaction=cast(Any, _no_database))
    spec = json.loads(json.dumps(app.openapi(), sort_keys=True))
    spec["paths"] = {
        path: item for path, item in spec["paths"].items() if not path.startswith("/dev")
    }
    kept = _closure(spec, _refs(spec["paths"]) | {"Error"})
    schemas = spec.get("components", {}).get("schemas", {})
    spec["components"]["schemas"] = {
        name: schemas[name] for name in sorted(kept) if name in schemas
    }
    return cast(dict[str, Any], spec)


def _refs(node: Any) -> set[str]:
    found: set[str] = set()
    if isinstance(node, dict):
        ref = node.get("$ref")
        if isinstance(ref, str) and ref.startswith(_SCHEMA_REF):
            found.add(ref.removeprefix(_SCHEMA_REF))
        for value in node.values():
            found |= _refs(value)
    elif isinstance(node, list):
        for value in node:
            found |= _refs(value)
    return found


def _closure(spec: dict[str, Any], names: Iterable[str]) -> set[str]:
    schemas = spec.get("components", {}).get("schemas", {})
    seen: set[str] = set()
    todo = list(names)
    while todo:
        name = todo.pop()
        if name in seen or name not in schemas:
            continue
        seen.add(name)
        todo.extend(_refs(schemas[name]))
    return seen


def _signature(schema: Any) -> str:
    def strip(node: Any) -> Any:
        if isinstance(node, dict):
            return {k: strip(v) for k, v in node.items() if k not in _NOISE}
        if isinstance(node, list):
            return [strip(v) for v in node]
        return node

    return json.dumps(strip(schema), sort_keys=True)


def _operations(spec: dict[str, Any]) -> dict[tuple[str, str], dict[str, Any]]:
    return {
        (path, method): operation
        for path, item in spec.get("paths", {}).items()
        for method, operation in item.items()
        if method in _METHODS
    }


def breaking_changes(old: dict[str, Any], new: dict[str, Any]) -> list[str]:
    """Every way `new` breaks a caller built against `old`."""
    problems: list[str] = []
    old_ops, new_ops = _operations(old), _operations(new)
    for (path, method), operation in sorted(old_ops.items()):
        label = f"{method.upper()} {path}"
        if (path, method) not in new_ops:
            problems.append(f"{label}: endpoint removed")
            continue
        before = {(p["in"], p["name"]): p for p in operation.get("parameters", [])}
        after = {(p["in"], p["name"]): p for p in new_ops[(path, method)].get("parameters", [])}
        for (where, name), parameter in sorted(before.items()):
            if (where, name) not in after:
                problems.append(f"{label}: {where} parameter {name} removed")
            elif after[(where, name)].get("required") and not parameter.get("required"):
                problems.append(f"{label}: {where} parameter {name} is now required")
        for (where, name), parameter in sorted(after.items()):
            if (where, name) not in before and parameter.get("required"):
                problems.append(f"{label}: new required {where} parameter {name}")

    old_schemas = old.get("components", {}).get("schemas", {})
    new_schemas = new.get("components", {}).get("schemas", {})
    responses = _closure(old, _refs([op.get("responses", {}) for op in old_ops.values()]))
    requests = _closure(new, _refs([op.get("requestBody", {}) for op in new_ops.values()]))
    for name in sorted(set(old_schemas) & set(new_schemas)):
        before_schema, after_schema = old_schemas[name], new_schemas[name]
        before_fields = before_schema.get("properties", {})
        after_fields = after_schema.get("properties", {})
        if name in responses:
            for field, schema in sorted(before_fields.items()):
                if field not in after_fields:
                    problems.append(f"{name}.{field}: removed from a response")
                elif _signature(schema) != _signature(after_fields[field]):
                    problems.append(f"{name}.{field}: type changed in a response")
        if name in requests:
            newly = set(after_schema.get("required", [])) - set(before_schema.get("required", []))
            problems.extend(f"{name}.{field}: now required in a request" for field in sorted(newly))
    return problems


def _encode(spec: dict[str, Any]) -> str:
    return json.dumps(spec, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m api.contract", description=__doc__.splitlines()[0]
    )
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument(
        "--check", action="store_true", help="refuse breaking or unrecorded changes"
    )
    action.add_argument("--write", action="store_true", help="record the contract as it is now")
    args = parser.parse_args(argv)

    spec = current_spec()
    recorded = json.loads(SNAPSHOT.read_text(encoding="utf-8")) if SNAPSHOT.exists() else None
    problems = [] if recorded is None else breaking_changes(recorded, spec)
    if problems:
        print("Breaking changes to the frozen /v1 contract:", file=sys.stderr)
        for problem in problems:
            print(f"  {problem}", file=sys.stderr)
        print("Make the change additive, or plan a /v2 (API plan section 1).", file=sys.stderr)
        return 1
    if args.write:
        SNAPSHOT.write_text(_encode(spec), encoding="utf-8")
        print(f"Recorded the /v1 contract in {SNAPSHOT.name}.")
        return 0
    if recorded != spec:
        print(
            "The API changed additively but the contract is not recorded. "
            "Run: python -m api.contract --write, and commit docs/api/openapi-v1.json.",
            file=sys.stderr,
        )
        return 1
    print("The /v1 contract holds.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
