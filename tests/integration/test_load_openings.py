"""A2 through the API: loading twice creates nothing new, a TA lead sees every opening and each
recruiter only their own. Made-up jobs; everything rolls back."""

import csv
import uuid
from pathlib import Path

import pytest

from pipeline.load_openings import COLUMNS, LoadRefused, check_lines, load_openings, read_rows

from .conftest import sign_in


class ClientApi:
    """The loader's API calls, sent to the test app instead of over the network."""

    def __init__(self, client, headers):
        self._client = client
        self._headers = headers

    def get(self, path):
        response = self._client.get(path, headers=self._headers)
        return response.status_code, response.json()

    def post(self, path, body):
        response = self._client.post(path, json=body, headers=self._headers)
        return response.status_code, response.json()


def _file(tmp_path: Path, rows: list[list[str]]) -> Path:
    path = tmp_path / "jobs.csv"
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(COLUMNS)
        writer.writerows(rows)
    return path


@pytest.fixture
def brand() -> str:
    return f"Made-up Brand {uuid.uuid4().hex[:8]}"


def _visible(client, account: str, brand: str) -> list[tuple[str, str]]:
    openings = client.get("/v1/openings?limit=200", headers=sign_in(client, account)).json()
    return sorted((o["department"], o["owner_recruiter"]) for o in openings if o["brand"] == brand)


def test_loading_twice_creates_nothing_new_and_scoping_holds(api_client, tmp_path, brand):
    client, _connection = api_client
    lines = check_lines(
        read_rows(
            _file(
                tmp_path,
                [
                    [brand, "Sales", "A", "2", "dev|recruiter-a", "team-a", "2026-08-04"],
                    [brand, "Leasing", "B", "1", "dev|recruiter-b", "team-b", "2026-08-04"],
                ],
            )
        )
    )
    lead = ClientApi(client, sign_in(client, "ta-lead"))

    first = load_openings(lead, lines)
    assert [c["line"] for c in first["created"]] == [2, 3]
    assert (first["already_there"], first["refused"]) == (0, [])

    second = load_openings(lead, lines)
    assert (second["created"], second["already_there"], second["refused"]) == ([], 2, [])

    assert _visible(client, "ta-lead", brand) == [
        ("Leasing", "dev|recruiter-b"),
        ("Sales", "dev|recruiter-a"),
    ]
    assert _visible(client, "recruiter-a", brand) == [("Sales", "dev|recruiter-a")]
    assert _visible(client, "recruiter-b", brand) == [("Leasing", "dev|recruiter-b")]


def test_the_opening_keeps_the_criteria_version_from_the_file(api_client, tmp_path, brand):
    client, _connection = api_client
    lines = check_lines(
        read_rows(
            _file(tmp_path, [[brand, "Sales", "A", "2", "dev|recruiter-a", "team-a", "2026-08-04"]])
        )
    )
    load_openings(ClientApi(client, sign_in(client, "ta-lead")), lines)
    openings = client.get("/v1/openings?limit=200", headers=sign_in(client, "ta-lead")).json()
    (opening,) = [o for o in openings if o["brand"] == brand]
    assert opening["criteria_version_id"] == "2026-08-04"


def test_an_unknown_criteria_version_is_refused_by_line(api_client, tmp_path, brand):
    client, _connection = api_client
    lines = check_lines(
        read_rows(
            _file(tmp_path, [[brand, "Sales", "A", "2", "dev|recruiter-a", "team-a", "1999-01-01"]])
        )
    )
    result = load_openings(ClientApi(client, sign_in(client, "ta-lead")), lines)
    assert result["created"] == []
    assert result["refused"] == [
        {"line": 2, "status": 409, "detail": "No criteria version 1999-01-01."}
    ]
    assert _visible(client, "ta-lead", brand) == []


def test_a_file_with_one_bad_line_loads_nothing(api_client, tmp_path, brand):
    client, _connection = api_client
    path = _file(
        tmp_path,
        [
            [brand, "Sales", "A", "2", "dev|recruiter-a", "team-a", "2026-08-04"],
            [brand, "Leasing", "C", "1", "dev|recruiter-b", "team-b", "2026-08-04"],
        ],
    )
    with pytest.raises(LoadRefused, match="line 3: track must be A or B"):
        check_lines(read_rows(path))
    assert _visible(client, "ta-lead", brand) == []
