"""Loads TA's open jobs through the API, not straight into the database (A2: BR-401, BR-410).

    python -m pipeline.load_openings JOBS.csv --api https://test-server --token-env TALENT_API_TOKEN
    python -m pipeline.load_openings JOBS.xlsx --api http://127.0.0.1:8090 --dev-account ta-lead

One line per open job, with a header row naming these columns, in any order:

    brand, department, track, headcount, owner_recruiter, team, criteria_version

The file stays outside the repository; the loader refuses one inside it. Every line is checked
before anything is sent: if any line has a missing or invalid value, the whole file is refused and
each problem is reported by line number, so nothing is ever loaded half-filled.

Running the load again creates nothing new: a line matching an open opening already visible to the
account loading (same brand, department, track, headcount, owner, team and criteria version) is
skipped. Load as a TA lead, who can own openings for every recruiter.
"""

import argparse
import csv
import json
import os
import re
import sys
import urllib.error
import urllib.request
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

import openpyxl

from replay.baseline import inside_git_repository

COLUMNS = (
    "brand",
    "department",
    "track",
    "headcount",
    "owner_recruiter",
    "team",
    "criteria_version",
)
_VERSION = re.compile(r"^[0-9A-Za-z._-]{1,64}$")
_PAGE = 200


class LoadRefused(Exception):
    def __init__(self, problems: Sequence[str]) -> None:
        super().__init__("; ".join(problems))
        self.problems = list(problems)


@dataclass(frozen=True, slots=True)
class OpeningLine:
    line: int
    brand: str
    department: str
    track: str
    headcount: int
    owner_recruiter: str
    team: str
    criteria_version: str

    def key(self) -> tuple[str, str, str, int, str, str, str]:
        return (
            self.brand,
            self.department,
            self.track,
            self.headcount,
            self.owner_recruiter,
            self.team,
            self.criteria_version,
        )

    def body(self) -> dict[str, Any]:
        return {
            "brand": self.brand,
            "department": self.department,
            "track": self.track,
            "headcount": self.headcount,
            "owner_recruiter": self.owner_recruiter,
            "team": self.team,
            "criteria_version_id": self.criteria_version,
        }


def read_rows(path: Path) -> list[tuple[int, dict[str, Any]]]:
    """(line number, values by lower-cased header) for every non-blank line after the header."""
    if path.suffix.lower() == ".xlsx":
        workbook = openpyxl.load_workbook(path, read_only=True, data_only=True)
        try:
            cells = [list(row) for row in workbook.worksheets[0].iter_rows(values_only=True)]
        finally:
            workbook.close()
    else:
        with path.open(encoding="utf-8-sig", newline="") as handle:
            cells = [list(row) for row in csv.reader(handle)]
    if not cells:
        raise LoadRefused(["the file is empty"])
    header = [("" if name is None else str(name)).strip().lower() for name in cells[0]]
    missing = [column for column in COLUMNS if column not in header]
    if missing:
        raise LoadRefused([f"line 1: missing columns {', '.join(missing)}"])
    rows = []
    for number, values in enumerate(cells[1:], start=2):
        if all(value is None or not str(value).strip() for value in values):
            continue
        rows.append(
            (
                number,
                {name: values[i] if i < len(values) else None for i, name in enumerate(header)},
            )
        )
    return rows


def _text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def check_lines(rows: Sequence[tuple[int, dict[str, Any]]]) -> list[OpeningLine]:
    """Every line checked; any problem refuses the whole file, by line number and column only."""
    problems: list[str] = []
    lines: list[OpeningLine] = []
    seen: dict[tuple[str, str, str, int, str, str, str], int] = {}
    for number, values in rows:
        found = [problem for problem in _line_problems(values)]
        if found:
            problems.extend(f"line {number}: {problem}" for problem in found)
            continue
        line = OpeningLine(
            line=number,
            brand=_text(values["brand"]),
            department=_text(values["department"]),
            track=_text(values["track"]).upper(),
            headcount=int(float(_text(values["headcount"]))),
            owner_recruiter=_text(values["owner_recruiter"]),
            team=_text(values["team"]),
            criteria_version=_text(values["criteria_version"]),
        )
        if line.key() in seen:
            problems.append(f"line {number}: the same job as line {seen[line.key()]}")
            continue
        seen[line.key()] = number
        lines.append(line)
    if not rows:
        problems.append("the file has no job lines")
    if problems:
        raise LoadRefused(problems)
    return lines


def _line_problems(values: dict[str, Any]) -> list[str]:
    problems = [f"{column} is missing" for column in COLUMNS if not _text(values.get(column))]
    track = _text(values.get("track")).upper()
    if track and track not in ("A", "B"):
        problems.append("track must be A or B")
    headcount = _text(values.get("headcount"))
    if headcount:
        try:
            parsed = float(headcount)
        except ValueError:
            parsed = -1.0
        if not parsed.is_integer() or not 0 < parsed <= 10_000:
            problems.append("headcount must be a whole number from 1 to 10000")
    version = _text(values.get("criteria_version"))
    if version and not _VERSION.match(version):
        problems.append("criteria_version is not a version id")
    for column in ("brand", "department", "owner_recruiter", "team"):
        if len(_text(values.get(column))) > 200:
            problems.append(f"{column} is longer than 200 characters")
    return problems


class Api(Protocol):
    def get(self, path: str) -> tuple[int, Any]: ...

    def post(self, path: str, body: dict[str, Any]) -> tuple[int, Any]: ...


class HttpApi:
    def __init__(self, base_url: str, token: str) -> None:
        self._base = base_url.rstrip("/")
        self._token = token

    def _send(self, method: str, path: str, body: dict[str, Any] | None = None) -> tuple[int, Any]:
        request = urllib.request.Request(
            self._base + path,
            data=None if body is None else json.dumps(body).encode("utf-8"),
            method=method,
            headers={"Authorization": f"Bearer {self._token}", "Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return response.status, json.loads(response.read() or b"null")
        except urllib.error.HTTPError as exc:
            payload = exc.read()
            try:
                return exc.code, json.loads(payload or b"null")
            except json.JSONDecodeError:
                return exc.code, None

    def get(self, path: str) -> tuple[int, Any]:
        return self._send("GET", path)

    def post(self, path: str, body: dict[str, Any]) -> tuple[int, Any]:
        return self._send("POST", path, body)


def _existing_open(api: Api) -> set[tuple[str, str, str, int, str, str, str]]:
    keys = set()
    offset = 0
    while True:
        status, page = api.get(f"/v1/openings?limit={_PAGE}&offset={offset}")
        if status != 200:
            raise LoadRefused([f"could not list existing openings (HTTP {status})"])
        for o in page:
            if o["status"] == "open":
                keys.add(
                    (
                        o["brand"],
                        o["department"],
                        o["track"],
                        o["headcount"],
                        o["owner_recruiter"],
                        o["team"],
                        o["criteria_version_id"],
                    )
                )
        if len(page) < _PAGE:
            return keys
        offset += _PAGE


def load_openings(api: Api, lines: Sequence[OpeningLine]) -> dict[str, Any]:
    existing = _existing_open(api)
    created, already, refused = [], 0, []
    for line in lines:
        if line.key() in existing:
            already += 1
            continue
        status, body = api.post("/v1/openings", line.body())
        if status == 201:
            created.append({"line": line.line, "opening_id": body["id"]})
            existing.add(line.key())
        else:
            detail = body.get("detail") if isinstance(body, dict) else None
            refused.append(
                {
                    "line": line.line,
                    "status": status,
                    "detail": detail if isinstance(detail, str) else None,
                }
            )
    return {"lines": len(lines), "created": created, "already_there": already, "refused": refused}


def _dev_token(base_url: str, account: str) -> str:
    request = urllib.request.Request(
        base_url.rstrip("/") + "/dev/token",
        data=json.dumps({"account": account}).encode("utf-8"),
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return str(json.loads(response.read())["access_token"])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m pipeline.load_openings")
    parser.add_argument("path", type=Path, help="CSV or XLSX, outside the repository")
    parser.add_argument("--api", required=True, help="the platform's base URL")
    sign_in = parser.add_mutually_exclusive_group(required=True)
    sign_in.add_argument("--token-env", help="environment variable holding a sign-in token")
    sign_in.add_argument("--dev-account", help="dev only: sign in as a fake account, e.g. ta-lead")
    parser.add_argument("--check-only", action="store_true", help="check the file, send nothing")
    args = parser.parse_args(argv)

    path = args.path.expanduser()
    if inside_git_repository(path):
        print("error: the jobs file is inside a git repository; keep it outside.", file=sys.stderr)
        return 2
    try:
        lines = check_lines(read_rows(path))
    except (OSError, LoadRefused) as exc:
        problems = exc.problems if isinstance(exc, LoadRefused) else [type(exc).__name__]
        print("Refused. Nothing was loaded:", file=sys.stderr)
        for problem in problems:
            print(f"  {problem}", file=sys.stderr)
        return 1
    if args.check_only:
        print(f"{len(lines)} job lines, all valid. Nothing sent.")
        return 0

    if args.dev_account:
        token = _dev_token(args.api, args.dev_account)
    else:
        token = os.environ.get(args.token_env, "")
        if not token:
            print(f"error: {args.token_env} is not set.", file=sys.stderr)
            return 2
    try:
        result = load_openings(HttpApi(args.api, token), lines)
    except LoadRefused as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 1 if result["refused"] else 0


if __name__ == "__main__":
    sys.exit(main())
