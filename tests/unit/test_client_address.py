"""Whose request it is, for rate limiting (D-WEB-3).

Rate limits only work if the address is the candidate's. Anyone can send X-Forwarded-For, so it
counts only when we have been told how many proxies stand in front of us.
"""

from types import SimpleNamespace

import pytest

from api.limits import RateLimiter, Rule, client_address

RULE = Rule("test", requests=2, window_seconds=60)


def _request(forwarded: str | None = None, peer: str | None = "10.0.0.1"):
    headers = {"x-forwarded-for": forwarded} if forwarded else {}
    return SimpleNamespace(headers=headers, client=SimpleNamespace(host=peer) if peer else None)


def test_with_no_proxy_the_header_is_ignored_because_anyone_can_send_it():
    request = _request(forwarded="1.2.3.4", peer="10.0.0.1")
    assert client_address(request, trusted_hops=0) == "10.0.0.1"


def test_behind_one_proxy_the_address_that_proxy_saw_is_used():
    # The proxy appends what it saw; a candidate claiming to be someone else is ahead of that.
    request = _request(forwarded="9.9.9.9, 203.0.113.5", peer="10.0.0.1")
    assert client_address(request, trusted_hops=1) == "203.0.113.5"


def test_behind_two_proxies_we_step_back_two():
    request = _request(forwarded="9.9.9.9, 203.0.113.5, 172.16.0.9", peer="10.0.0.1")
    assert client_address(request, trusted_hops=2) == "203.0.113.5"


def test_a_short_chain_never_reads_past_its_start():
    request = _request(forwarded="203.0.113.5", peer="10.0.0.1")
    assert client_address(request, trusted_hops=3) == "203.0.113.5"


def test_no_address_at_all_is_one_bucket_not_an_error():
    assert client_address(_request(peer=None), trusted_hops=0) == "unknown"


@pytest.mark.parametrize("hops", [0, 1])
def test_one_candidate_cannot_use_up_another_candidates_allowance(hops):
    limiter = RateLimiter(clock=lambda: 0.0)
    noisy = client_address(_request("9.9.9.9, 198.51.100.7"), hops)
    quiet = client_address(_request("9.9.9.9, 198.51.100.8"), hops)
    for _ in range(RULE.requests):
        assert limiter.check(RULE, noisy) is None
    assert limiter.check(RULE, noisy) is not None
    # The other candidate is unaffected — but only when we can tell them apart.
    if hops:
        assert limiter.check(RULE, quiet) is None
    else:
        assert noisy == quiet  # without a trusted proxy they are one address to us
