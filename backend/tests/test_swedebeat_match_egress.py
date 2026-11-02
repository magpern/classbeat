"""The worker's catalogue client (ADR 0016 §4, B1): one host, no redirect, worker only.

``cloud.swedebeat.se`` is not on ``WORKER_HOSTS`` or the web list. The dedicated client is the
only caller, and outside the worker it is not built, so the web process cannot send the token.
"""

import pytest

from cataloguefake import CatalogueFake
from classbeat.egress import (
    SPOTIFY_HOSTS,
    SWEDEBEAT_CLOUD_ALLOWLIST,
    SWEDEBEAT_CLOUD_HOST,
    WEB_URLLIB_HOSTS,
    WORKER_HOSTS,
    OutboundBlocked,
    swedebeat_cloud_async_client,
    swedebeat_cloud_test_client,
)
from classbeat.swedebeat_match.search import search

TOKEN = "sbt-egress-token-not-real"


def test_the_catalogue_host_is_on_its_own_list_and_no_shared_list() -> None:
    assert SWEDEBEAT_CLOUD_ALLOWLIST.hosts == frozenset({SWEDEBEAT_CLOUD_HOST})
    assert SWEDEBEAT_CLOUD_HOST == "cloud.swedebeat.se"
    for hosts in (WORKER_HOSTS, WEB_URLLIB_HOSTS, SPOTIFY_HOSTS):
        assert SWEDEBEAT_CLOUD_HOST not in hosts


async def test_the_production_client_exists_only_in_the_worker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with pytest.raises(OutboundBlocked) as refused:
        swedebeat_cloud_async_client()
    assert (refused.value.host, refused.value.after_request) == (SWEDEBEAT_CLOUD_HOST, False)
    monkeypatch.setattr("classbeat.egress._worker_process", True)
    client = swedebeat_cloud_async_client()
    try:
        assert client.follow_redirects is False
        assert list(client.headers) == []
    finally:
        await client.aclose()


async def test_http_and_another_host_are_refused_before_a_request() -> None:
    fake = CatalogueFake()
    async with swedebeat_cloud_test_client(fake) as client:
        with pytest.raises(OutboundBlocked) as http:
            await client.get(f"http://{SWEDEBEAT_CLOUD_HOST}/track/extendedsearch")
        with pytest.raises(OutboundBlocked) as other:
            await client.get("https://evil.example/track/extendedsearch")
        with pytest.raises(OutboundBlocked) as www:
            await client.get("https://www.swedebeat.se/track/extendedsearch")
    assert http.value.after_request is False
    assert other.value.after_request is False
    assert www.value.after_request is False
    assert fake.calls == []


async def test_a_redirect_off_the_host_is_not_followed_with_the_token() -> None:
    fake = CatalogueFake(redirect="https://api.mowl.com/v1/ping")
    async with swedebeat_cloud_test_client(fake) as client:
        with pytest.raises(OutboundBlocked) as refused:
            await search(client, TOKEN, "Blue Monday", "New Order")
    assert refused.value.after_request is True
    assert len(fake.calls) == 1
    assert fake.calls[0].token == TOKEN
    assert "api.mowl.com" not in fake.calls[0].url
