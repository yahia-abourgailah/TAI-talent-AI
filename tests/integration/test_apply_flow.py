"""Applying from a job post (BR-101, BR-109, BR-602, CR-02): the whole way in, from the link a
candidate taps to an application that is scored by itself. Made-up people; everything rolls back.
"""

import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import text

from .conftest import sign_in

JOB = {
    "brand": "Made-up Brand",
    "department": "Sales",
    "track": "A",
    "headcount": 2,
    "team": "team-a",
    "title": "Sales Agent",
    "location": "New Cairo",
    "public": True,
}
CONTACT = {"type": "whatsapp", "value": "010" + "00000007"}


def _consent(client, **overrides) -> dict:
    wording = client.get("/v1/public/consent-wording").json()
    return {
        "agreed": True,
        "wording_version": wording["wording_version"],
        "purposes": wording["purposes"],
        "channels": ["whatsapp", "phone"],
        "language": "en",
        "agreed_at": datetime.now(UTC).isoformat(),
        **overrides,
    }


def _apply(client, body: dict, key: str | None = None, token: str | None = None):
    headers = {"Idempotency-Key": key or str(uuid.uuid4())}
    if token:
        headers["X-Upload-Token"] = token
    return client.post("/v1/public/applications", json=body, headers=headers)


@pytest.fixture
def job(api_client):
    """A public requisition with a job post, as a recruiter publishes it."""
    client, _connection = api_client
    headers = sign_in(client, "recruiter-a")
    requisition = client.post("/v1/requisitions", json=JOB, headers=headers)
    assert requisition.status_code == 201, requisition.text
    post = client.post(
        f"/v1/requisitions/{requisition.json()['id']}/job-posts",
        json={"channel": "tiktok", "label": "September sales post"},
        headers={**headers, "Idempotency-Key": str(uuid.uuid4())},
    )
    assert post.status_code == 201, post.text
    return requisition.json(), post.json()


def _body(requisition: dict, client, **overrides) -> dict:
    return {
        "requisition_id": requisition["id"],
        "fields": {"full_name": "Fake Applicant", "location": "New Cairo", "age": "26"},
        "contact_channel": CONTACT,
        "consent": _consent(client),
        **overrides,
    }


def test_a_candidate_applies_from_a_job_post_and_is_scored_without_a_recruiter(api_client, job):
    client, connection = api_client
    requisition, post = job

    listed = client.get("/v1/public/requisitions").json()["items"]
    (shown,) = [row for row in listed if row["requisition_id"] == requisition["id"]]
    assert (shown["title"], shown["location"], shown["brand"]) == (
        "Sales Agent",
        "New Cairo",
        "Made-up Brand",
    )
    assert set(shown) == {"requisition_id", "title", "brand", "department", "location", "track"}

    sent = _apply(client, _body(requisition, client, tracking_code=post["tracking_code"]))
    assert sent.status_code == 201, sent.text
    answer = sent.json()
    assert answer["status"] == "received"
    assert answer["application_id"].startswith("app_")
    assert set(answer) == {"application_id", "status"}  # never a score or a tier

    number = int(answer["application_id"].removeprefix("app_"))
    application = connection.execute(
        text("SELECT candidate_id, owner_recruiter, team FROM pipeline.application WHERE id = :id"),
        {"id": number},
    ).one()
    assert application.owner_recruiter == "dev|recruiter-a"

    # Creating the application starts the pipeline and queues the scoring, with nobody involved.
    stage = connection.execute(
        text("SELECT current_step FROM pipeline.application_state WHERE id = :id"), {"id": number}
    ).scalar_one()
    queued = connection.execute(
        text(
            "SELECT count(*) FROM jobs.job WHERE kind = 'score_application' "
            "AND params->>'application_id' = :id"
        ),
        {"id": str(number)},
    ).scalar_one()
    assert (stage, queued) == ("new", 1)

    consent = connection.execute(
        text(
            "SELECT wording_version, channels, language, tracking_code, source, application_id "
            "FROM core.consent WHERE candidate_id = :c"
        ),
        {"c": application.candidate_id},
    ).one()
    assert consent.tracking_code == post["tracking_code"]
    assert (consent.language, consent.source, consent.application_id) == (
        "en",
        "public_apply",
        number,
    )
    assert sorted(consent.channels) == ["phone", "whatsapp"]

    fields = dict(
        connection.execute(
            text("SELECT field, source FROM core.candidate_field_current WHERE candidate_id = :c"),
            {"c": application.candidate_id},
        ).all()
    )
    assert fields["full_name"] == "candidate_confirmed"
    assert fields["whatsapp"] == "candidate_confirmed"


def test_a_cv_upload_and_the_application_are_one_candidate(api_client, job):
    client, connection = api_client
    requisition, _post = job
    upload = client.post(
        "/v1/public/cv-uploads",
        files={"file": ("cv.pdf", b"%PDF-1.4 FAKE-OCR:en made-up cv", "application/pdf")},
    )
    assert upload.status_code == 201, upload.text
    uploaded = upload.json()

    sent = _apply(
        client,
        _body(requisition, client, upload_id=uploaded["upload_id"]),
        token=uploaded["upload_token"],
    )
    assert sent.status_code == 201, sent.text
    number = int(sent.json()["application_id"].removeprefix("app_"))
    candidate = connection.execute(
        text("SELECT candidate_id FROM pipeline.application WHERE id = :id"), {"id": number}
    ).scalar_one()
    from_upload = connection.execute(
        text("SELECT candidate_id FROM intake.cv_upload WHERE id = :id"),
        {"id": int(uploaded["upload_id"].removeprefix("upl_"))},
    ).scalar_one()
    assert candidate == from_upload

    without_token = _apply(client, _body(requisition, client, upload_id=uploaded["upload_id"]))
    assert without_token.status_code == 400
    assert without_token.json()["error"]["code"] == "upload_token_required"


@pytest.mark.parametrize(
    ("change", "status", "code"),
    [
        ({"consent": {"agreed": False}}, 400, "consent_required"),
        ({"consent": {"wording_version": "made-up-wording"}}, 400, "consent_wording_changed"),
        ({"consent": {"channels": []}}, 400, "consent_channels_invalid"),
        ({"tracking_code": "made-up-code"}, 404, "not_found"),
        ({"fields": {"salary": "a lot"}}, 400, "invalid_request"),
    ],
)
def test_an_application_that_cannot_be_kept_is_refused(api_client, job, change, status, code):
    client, _connection = api_client
    requisition, _post = job
    body = _body(requisition, client)
    for name, value in change.items():
        body[name] = (
            {**body[name], **value} if isinstance(value, dict) and name == "consent" else value
        )
    response = _apply(client, body)
    assert response.status_code == status, response.text
    if code is not None:
        assert response.json()["error"]["code"] == code


def test_an_application_needs_a_way_to_reach_the_candidate(api_client, job):
    client, _connection = api_client
    requisition, _post = job
    body = _body(requisition, client)
    del body["contact_channel"]
    missing = _apply(client, body)
    assert missing.status_code == 400
    assert missing.json()["error"]["code"] == "invalid_request"

    # Email alone is not a way to reach someone for a job (BR-109).
    body = _body(requisition, client, contact_channel={"type": "email", "value": "x@example.com"})
    assert _apply(client, body).status_code == 400


def test_a_retried_application_is_sent_once(api_client, job):
    client, _connection = api_client
    requisition, _post = job
    key = str(uuid.uuid4())
    body = _body(requisition, client)
    first = _apply(client, body, key=key)
    again = _apply(client, body, key=key)
    assert (first.status_code, again.status_code) == (201, 201)
    assert again.json() == first.json()
    assert again.headers["idempotent-replayed"] == "true"

    missing_key = client.post("/v1/public/applications", json=_body(requisition, client))
    assert missing_key.status_code == 400
    assert missing_key.json()["error"]["code"] == "idempotency_key_required"


def test_a_job_nobody_published_cannot_be_applied_to(api_client):
    client, _connection = api_client
    headers = sign_in(client, "recruiter-a")
    private = client.post("/v1/requisitions", json={**JOB, "public": False}, headers=headers).json()
    assert client.get(f"/v1/public/requisitions/{private['id']}").status_code == 404
    listed = client.get("/v1/public/requisitions").json()["items"]
    assert private["id"] not in {row["requisition_id"] for row in listed}
    assert _apply(client, _body(private, client)).status_code == 404


def test_the_consent_wording_is_shown_in_both_languages(api_client):
    client, _connection = api_client
    wording = client.get("/v1/public/consent-wording")
    assert wording.status_code == 200
    body = wording.json()
    assert body["provisional"] is True  # until Legal approves one (D-WEB-4)
    assert body["purposes"] == ["recruitment_contact"]
    assert body["text_ar"] and body["text_en"]
    assert wording.headers["cache-control"] == "no-store"


def test_applying_twice_to_the_same_job_is_answered_not_failed(api_client, job):
    """A second click, a reopened tab, a change of mind: an ordinary thing for a person to do.

    One person applies to one job once (migration 0005), and the way a public application is one
    person twice is the CV: the same file is the same candidate (BR-106). Before this, the second
    attempt reached the database and came back as an internal error, which tells the candidate
    nothing and the recruiter less.
    """
    client, _connection = api_client
    requisition, _post = job
    upload = client.post(
        "/v1/public/cv-uploads",
        files={"file": ("cv.pdf", b"%PDF-1.4 FAKE-OCR:en applying twice", "application/pdf")},
    )
    uploaded = upload.json()
    body = _body(requisition, client, upload_id=uploaded["upload_id"])

    first = _apply(client, body, token=uploaded["upload_token"])
    assert first.status_code == 201, first.text

    again = _apply(
        client,
        _body(requisition, client, upload_id=uploaded["upload_id"]),
        token=uploaded["upload_token"],
    )
    assert again.status_code == 409, again.text
    answer = again.json()["error"]
    assert answer["code"] == "already_applied"
    assert "already applied" in answer["message"]
    assert "request_id" in answer


def test_an_application_says_where_it_came_from(api_client, job):
    """A recruiter looking at an application should not have to ask (BR-602)."""
    client, _connection = api_client
    requisition, post = job
    through_the_link = _apply(
        client, _body(requisition, client, tracking_code=post["tracking_code"])
    ).json()["application_id"]
    straight_to_the_page = _apply(client, _body(requisition, client)).json()["application_id"]

    headers = sign_in(client, "recruiter-a")

    def arrival(application_id: str) -> dict:
        return client.get(f"/v1/applications/{application_id}", headers=headers).json()[
            "arrived_from"
        ]

    assert arrival(through_the_link) == {
        "source": "job_post",
        "channel": "tiktok",
        "tracking_code": post["tracking_code"],
        "label": "September sales post",
    }
    assert arrival(straight_to_the_page)["source"] == "careers_page"


def test_a_candidate_a_recruiter_added_says_so(api_client, make_candidate):
    """Nobody applied: a recruiter put them on the requisition, and the row says that rather
    than implying they came through the careers page."""
    client, connection = api_client
    headers = sign_in(client, "ta-lead")
    requisition = client.post("/v1/requisitions", json=JOB, headers=headers).json()
    candidate = make_candidate(connection, full_name="A Recruiter's Own Find")
    application = client.post(
        "/v1/applications",
        json={"requisition_id": requisition["id"], "candidate_id": f"cand_{candidate}"},
        headers={**headers, "Idempotency-Key": str(uuid.uuid4())},
    ).json()
    assert application["arrived_from"]["source"] == "recruiter"
