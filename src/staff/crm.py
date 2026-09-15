"""The employee roster from the company CRM, read-only (DEP-04).

    GET {TALENT_CRM_BASE_URL}/api/learning-integration/get_users?filter[status]=active

The same endpoint the L&D reports use. The key goes in X-Learning-Key only: a key sent in
Authorization reads as missing. Pages follow meta.has_more_pages. Only active employees are asked
for, since someone who has left is not own staff and their details are not needed.

Nothing is stored here: the payloads become match keys and are dropped. Errors name the status code
and the CRM's error code, never a response value or the key.
"""

import time
from collections.abc import Callable
from typing import Any

import httpx

from staff.identity import email_key, name_key, phone_key
from staff.roster import ACTIVE_STATUSES, Employee, Roster

USERS_PATH = "/api/learning-integration/get_users"
KEY_HEADER = "X-Learning-Key"
PER_PAGE = 200
MAX_PAGES = 1000
RATE_LIMIT_RETRIES = 3


class CrmError(Exception):
    """The CRM call failed or answered in an unexpected shape. Carries no response values."""


def normalise_base_url(value: str) -> str:
    """Accept the host with or without a trailing /api: USERS_PATH already starts with /api."""
    trimmed = value.strip().rstrip("/")
    return trimmed[: -len("/api")] if trimmed.endswith("/api") else trimmed


def _employee(payload: dict[str, Any]) -> Employee | None:
    identifier = next(
        (
            str(payload[key]).strip()
            for key in ("employee_code", "odoo_id", "id")
            if payload.get(key) not in (None, "")
        ),
        None,
    )
    if identifier is None:
        return None
    return Employee(
        employee_id=identifier,
        active=str(payload.get("status") or "").strip().casefold() in ACTIVE_STATUSES,
        phone=phone_key(payload.get("mobile")),
        email=email_key(payload.get("email")),
        name=name_key(payload.get("full_name") or payload.get("name")),
    )


def _page(client: httpx.Client, page: int, sleep: Callable[[float], None]) -> dict[str, Any]:
    params: dict[str, str | int] = {"page": page, "per_page": PER_PAGE, "filter[status]": "active"}
    for attempt in range(RATE_LIMIT_RETRIES + 1):
        try:
            response = client.get(USERS_PATH, params=params)
        except httpx.HTTPError as exc:
            raise CrmError(f"Could not reach the CRM: {type(exc).__name__}.") from exc
        if response.status_code != httpx.codes.TOO_MANY_REQUESTS or attempt == RATE_LIMIT_RETRIES:
            break
        sleep(2.0 * (attempt + 1))

    if response.status_code != httpx.codes.OK:
        code = ""
        try:
            body = response.json()
            if isinstance(body, dict) and isinstance(body.get("error"), str):
                code = f" ({body['error'][:60]})"
        except ValueError:
            pass
        hint = f"; the key must be sent in {KEY_HEADER}" if response.status_code == 401 else ""
        raise CrmError(f"The CRM answered {response.status_code}{code}{hint}.")

    try:
        body = response.json()
    except ValueError as exc:
        raise CrmError("The CRM answer was not JSON.") from exc
    if (
        not isinstance(body, dict)
        or not isinstance(body.get("users"), list)
        or not isinstance(body.get("meta"), dict)
    ):
        raise CrmError("The CRM answer did not carry `users` and `meta`.")
    return body


def fetch_roster(
    base_url: str,
    service_key: str,
    *,
    transport: httpx.BaseTransport | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> Roster:
    if not base_url or not service_key:
        raise CrmError("Set TALENT_CRM_BASE_URL and TALENT_CRM_SERVICE_KEY in .env.")
    employees: list[Employee] = []
    without_id = 0
    with httpx.Client(
        base_url=normalise_base_url(base_url),
        headers={KEY_HEADER: service_key, "Accept": "application/json"},
        timeout=60.0,
        follow_redirects=False,
        transport=transport,
    ) as client:
        for page in range(1, MAX_PAGES + 1):
            body = _page(client, page, sleep)
            users = body["users"]
            for payload in users:
                employee = _employee(payload) if isinstance(payload, dict) else None
                if employee is None:
                    without_id += 1
                else:
                    employees.append(employee)
            if not body["meta"].get("has_more_pages"):
                return Roster(tuple(employees), without_id)
            if not users:
                raise CrmError(f"The CRM said more pages follow page {page}, but it was empty.")
    raise CrmError(f"The CRM roster did not end within {MAX_PAGES} pages.")
