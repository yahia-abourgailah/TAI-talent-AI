"""Webhook signing, the event body and the delivery settings, without a database."""

import hashlib
import hmac
import json
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from api.errors import ApiError
from api.pages import decode_cursor, encode_cursor
from integrations.events import envelope
from integrations.webhooks import RETRY_GAPS_SECONDS, encode_body, signature, verify

SECRET = "test-webhook-secret-0123456789abcdef"
NOW = 1_760_000_000


def test_the_signature_is_hmac_sha256_over_timestamp_dot_body():
    body = b'{"id":"evt_1"}'
    expected = hmac.new(SECRET.encode(), b"1760000000." + body, hashlib.sha256).hexdigest()
    assert signature(SECRET, NOW, body) == f"sha256={expected}"


def test_the_crm_accepts_only_a_matching_recent_signature():
    body = b'{"id":"evt_1"}'
    good = signature(SECRET, NOW, body)
    assert verify(SECRET, str(NOW), body, good, now=NOW + 10)
    assert not verify(SECRET, str(NOW), body + b" ", good, now=NOW)
    assert not verify("another-secret-0123456789abcdef0123", str(NOW), body, good, now=NOW)
    assert not verify(SECRET, str(NOW), body, good, now=NOW + 301)
    assert not verify(SECRET, "not-a-time", body, good, now=NOW)


def test_an_event_body_carries_ids_and_codes_only():
    row = {
        "id": 7,
        "type": "application.stage_changed",
        "occurred_at": datetime(2026, 10, 21, 11, 2, 33, tzinfo=UTC),
        "data": {"application_id": "app_4", "from_stage": "new", "to_stage": "contacted"},
    }
    event = envelope(row)
    assert set(event) == {"id", "type", "api_version", "occurred_at", "data"}
    assert (event["id"], event["api_version"], event["occurred_at"]) == (
        "evt_7",
        "v1",
        "2026-10-21T11:02:33Z",
    )
    assert json.loads(encode_body(event)) == event


def test_retries_grow_as_the_plan_says():
    assert RETRY_GAPS_SECONDS == (60, 300, 1800, 7200, 21600)


def test_feed_cursors_do_not_mix_with_list_cursors():
    assert decode_cursor(encode_cursor(5, "after"), "after") == 5
    with pytest.raises(ApiError):
        decode_cursor(encode_cursor(5), "after")


def test_delivery_is_off_until_a_url_is_set(make_settings):
    assert make_settings().crm_webhook_url == ""


def test_a_webhook_url_needs_a_long_secret(make_settings):
    with pytest.raises(ValidationError, match="TALENT_CRM_WEBHOOK_SECRET"):
        make_settings(crm_webhook_url="https://crm.example.com/hooks/talent")
    with pytest.raises(ValidationError, match="TALENT_CRM_WEBHOOK_SECRET"):
        make_settings(crm_webhook_url="https://crm.example.com/hooks", crm_webhook_secret="short")


@pytest.mark.parametrize(
    ("env", "url"),
    [
        ("staging", "http://crm.example.com/hooks"),
        ("dev", "http://crm.example.com/hooks"),
        ("dev", "http://localhost.example.com/hooks"),
        ("staging", "http://localhost:9999/hooks"),
    ],
)
def test_a_webhook_url_must_be_https_except_to_localhost_in_dev(make_settings, env, url):
    with pytest.raises(ValidationError, match="https"):
        make_settings(env=env, crm_webhook_url=url, crm_webhook_secret=SECRET)


def test_accepted_webhook_urls(make_settings):
    https = make_settings(crm_webhook_url="https://crm.example.com/h", crm_webhook_secret=SECRET)
    local = make_settings(crm_webhook_url="http://127.0.0.1:9999/h", crm_webhook_secret=SECRET)
    assert https.crm_webhook_url and local.crm_webhook_url
    assert SECRET not in repr(https)
