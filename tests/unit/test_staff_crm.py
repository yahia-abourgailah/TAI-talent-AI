"""Reading the employee roster from the CRM, against a fake CRM. No network."""

import httpx
import pytest

from staff.crm import KEY_HEADER, USERS_PATH, CrmError, fetch_roster, normalise_base_url

KEY = "test-key-not-real"
# Fabricated numbers that belong to no one.
MOBILE = "010" + "00000001"


def _page(users, more=False):
    return {
        "users": users,
        "meta": {"current_page": 1, "has_more_pages": more, "total": len(users)},
    }


def _fetch(handler, **kwargs):
    return fetch_roster(
        "https://crm.example.com/api",
        KEY,
        transport=httpx.MockTransport(handler),
        sleep=lambda _: None,
        **kwargs,
    )


@pytest.mark.parametrize(
    "given",
    [
        "https://crm.example.com",
        "https://crm.example.com/",
        "https://crm.example.com/api",
        " https://crm.example.com/api/ ",
    ],
)
def test_the_base_url_is_accepted_with_or_without_api(given):
    assert normalise_base_url(given) == "https://crm.example.com"


def test_asks_for_active_employees_with_the_key_in_its_header():
    seen: list[httpx.Request] = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, json=_page([]))

    _fetch(handler)
    (request,) = seen
    assert request.url.path == USERS_PATH
    assert request.headers[KEY_HEADER] == KEY
    assert "authorization" not in request.headers
    assert request.url.params["filter[status]"] == "active"


def test_walks_every_page_and_builds_match_keys():
    pages = {
        "1": _page(
            [
                {
                    "id": 1,
                    "employee_code": "E-1",
                    "full_name": "Fake Staff One",
                    "mobile": "+20 100 000 0001",
                    "email": "Fake.One@example.com",
                    "status": "active",
                },
                {"id": 2, "odoo_id": "77", "name": "Fake Staff Two", "status": "active"},
            ],
            more=True,
        ),
        "2": _page([{"name": "No Identifier", "status": "active"}, {"id": 3, "status": "Active"}]),
    }

    def handler(request):
        return httpx.Response(200, json=pages[request.url.params["page"]])

    roster = _fetch(handler)
    one, two, three = roster.employees
    assert (one.employee_id, one.phone, one.email, one.name) == (
        "E-1",
        MOBILE,
        "fake.one@example.com",
        "fake staff one",
    )
    assert two.employee_id == "77"  # no employee code: the Odoo id
    assert three.employee_id == "3" and three.active
    assert roster.rows_without_id == 1


def test_masked_contact_details_give_no_key():
    user = {"id": 1, "mobile": "(masked)", "email": "(masked)", "status": "active"}
    (employee,) = _fetch(lambda _: httpx.Response(200, json=_page([user]))).employees
    assert (employee.phone, employee.email) == (None, None)


def test_a_rejected_key_is_reported_without_the_key():
    def handler(_):
        return httpx.Response(401, json={"error": "unauthorized", "message": "bad key"})

    with pytest.raises(CrmError) as caught:
        _fetch(handler)
    assert "401 (unauthorized)" in str(caught.value)
    assert KEY not in str(caught.value)


def test_rate_limit_is_retried():
    answers = iter([httpx.Response(429), httpx.Response(200, json=_page([]))])
    assert _fetch(lambda _: next(answers)).employees == ()


@pytest.mark.parametrize(
    "body",
    [{"data": []}, {"users": [], "meta": "x"}, [1, 2]],
)
def test_an_unexpected_shape_is_refused(body):
    with pytest.raises(CrmError, match="users"):
        _fetch(lambda _: httpx.Response(200, json=body))


def test_an_empty_page_that_promises_more_stops():
    with pytest.raises(CrmError, match="empty"):
        _fetch(lambda _: httpx.Response(200, json=_page([], more=True)))


def test_the_check_reads_the_crm_when_it_is_configured(monkeypatch, tmp_path):
    import openpyxl

    from replay.workbook import REQUIRED_COLUMNS
    from staff import check
    from staff.crm import _employee
    from staff.roster import Roster

    columns = (*REQUIRED_COLUMNS, "Stage")
    workbook = openpyxl.Workbook()
    workbook.active.append(list(columns))
    row = {"Name": "Fake Staff Two", "Employer": "Nawy", "Phone Number": MOBILE}
    workbook.active.append([row.get(column) for column in columns])
    master = tmp_path / "data" / "TAI_Master.xlsx"
    master.parent.mkdir()
    workbook.save(master)

    employee = _employee({"id": 9, "employee_code": "E-9", "mobile": MOBILE, "status": "active"})
    calls: list[tuple[str, str]] = []

    def fake_fetch(url, key):
        calls.append((url, key))
        return Roster((employee,), 0)

    monkeypatch.setenv("TALENT_CRM_BASE_URL", "https://crm.example.com/api")
    monkeypatch.setenv("TALENT_CRM_SERVICE_KEY", KEY)
    monkeypatch.delenv("TALENT_EMPLOYEES_PATH", raising=False)
    monkeypatch.setattr(check, "fetch_roster", fake_fetch)
    report = tmp_path / "report.md"
    argv = ["--master", str(master), "--out", str(tmp_path / "out"), "--report-copy", str(report)]
    assert check.main(argv) == 1
    assert calls == [("https://crm.example.com/api", KEY)]
    content = report.read_text()
    assert "Roster from the CRM: 1 employees, 1 active" in content
    assert "**Active employees the criteria did not exclude:** sheet row 2." in content
    assert KEY not in content and "E-9" not in content


def test_missing_settings_are_refused():
    with pytest.raises(CrmError, match="TALENT_CRM_SERVICE_KEY"):
        fetch_roster("https://crm.example.com", "")
