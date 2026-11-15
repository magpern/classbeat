"""The player client (ADR 0034 section 2, #1058): one installed host, no redirect of any kind, a
total deadline and a cap on the answer's size. Every request goes to an ``httpx.MockTransport``
(netguard holds)."""

import asyncio
from collections.abc import AsyncIterator, Iterator

import httpx
import pytest

from classbeat import egress
from classbeat.egress import (
    PLAYER_ANSWER_MAX,
    AnswerTooLarge,
    OutboundBlocked,
    RequestDeadline,
    install_player_allowlist,
    player_allowlist,
    player_async_client,
    player_test_client,
)
from classbeat.player.base_url_policy import PlayerUrlRefused

BASE = "https://player.example"


@pytest.fixture(autouse=True)
def no_player() -> Iterator[None]:
    install_player_allowlist(None)
    yield
    install_player_allowlist(None)


def answering(*responses: httpx.Response) -> tuple[httpx.MockTransport, list[httpx.Request]]:
    seen: list[httpx.Request] = []
    queue = list(responses)

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return queue.pop(0)

    return httpx.MockTransport(handler), seen


async def test_the_client_posts_to_the_one_host_with_no_header_of_ours() -> None:
    transport, seen = answering(httpx.Response(200))
    async with player_test_client(transport) as client:
        answer = await client.post("/api/classbeat/events", data={"logout_token": "t"})
    assert answer.status_code == 200
    [request] = seen
    assert str(request.url) == f"{BASE}/api/classbeat/events"
    assert request.headers["content-type"] == "application/x-www-form-urlencoded"
    assert request.content == b"logout_token=t"
    assert "sec-fetch-site" not in request.headers
    assert "authorization" not in request.headers
    assert "cookie" not in request.headers
    assert not {"user-agent", "accept"} & set(request.headers)


async def test_another_host_is_refused_before_anything_is_sent() -> None:
    transport, seen = answering(httpx.Response(200))
    async with player_test_client(transport) as client:
        with pytest.raises(OutboundBlocked) as refused:
            await client.post("https://evil.example/api/classbeat/events")
        with pytest.raises(OutboundBlocked):
            await client.post("http://player.example/api/classbeat/events")
        with pytest.raises(OutboundBlocked):
            await client.post("https://player.example:8443/api/classbeat/events")
    assert seen == []
    assert refused.value.after_request is False


@pytest.mark.parametrize("status", [301, 302, 303, 307, 308, 300, 304])
@pytest.mark.parametrize("location", ["https://player.example/other", "https://evil.example/"])
async def test_every_redirect_is_refused_and_not_followed(status: int, location: str) -> None:
    transport, seen = answering(httpx.Response(status, headers={"location": location}))
    async with player_test_client(transport) as client:
        with pytest.raises(OutboundBlocked) as refused:
            await client.post("/api/classbeat/events", data={"logout_token": "t"})
    assert len(seen) == 1
    assert refused.value.after_request is True


@pytest.mark.parametrize(
    "length", [str(PLAYER_ANSWER_MAX + 1), "99999999999999999999", "-1", "1e3", " 5", "abc"]
)
async def test_a_large_or_odd_declared_length_is_refused(length: str) -> None:
    transport, _ = answering(httpx.Response(200, headers={"content-length": length}))
    async with player_test_client(transport) as client:
        with pytest.raises(AnswerTooLarge):
            await client.post("/api/classbeat/events")


class Trickle(httpx.AsyncByteStream):
    def __init__(self, chunks: int, size: int, pause: float = 0.0) -> None:
        self.chunks, self.size, self.pause = chunks, size, pause

    async def __aiter__(self) -> AsyncIterator[bytes]:
        for _ in range(self.chunks):
            if self.pause:
                await asyncio.sleep(self.pause)
            yield b"x" * self.size


async def test_an_undeclared_answer_is_cut_at_the_cap() -> None:
    big = httpx.Response(200, stream=Trickle(chunks=17, size=4096))  # 68 KiB, no length
    transport, _ = answering(big)
    async with player_test_client(transport) as client:
        with pytest.raises(AnswerTooLarge):
            await client.post("/api/classbeat/events")


async def test_an_answer_at_the_cap_is_read() -> None:
    fits = httpx.Response(200, stream=Trickle(chunks=16, size=4096))  # exactly 64 KiB
    transport, _ = answering(fits)
    async with player_test_client(transport) as client:
        answer = await client.post("/api/classbeat/events")
    assert len(answer.content) == PLAYER_ANSWER_MAX


async def test_a_trickling_answer_ends_at_the_total_deadline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(egress, "PLAYER_TOTAL_SECONDS", 0.2)
    slow = httpx.Response(200, stream=Trickle(chunks=100, size=1, pause=0.05))
    transport, _ = answering(slow)
    async with player_test_client(transport) as client:
        with pytest.raises(RequestDeadline):
            await client.post("/api/classbeat/events")


async def test_a_slow_first_answer_ends_at_the_total_deadline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(egress, "PLAYER_TOTAL_SECONDS", 0.1)

    async def stalled(request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(5)
        return httpx.Response(200)

    async with player_test_client(httpx.MockTransport(stalled)) as client:
        with pytest.raises(TimeoutError):
            await client.post("/api/classbeat/events")


def test_without_an_installed_host_the_client_refuses() -> None:
    assert player_allowlist() is None
    with pytest.raises(OutboundBlocked):
        player_async_client()


async def test_the_installed_host_is_the_clients_only_one() -> None:
    install_player_allowlist("https://events.player.example/")
    allowlist = player_allowlist()
    assert allowlist is not None
    async with player_async_client() as client:
        assert str(client.base_url) == "https://events.player.example"
        assert client.follow_redirects is False
        assert client.trust_env is False
    install_player_allowlist(None)
    assert player_allowlist() is None


@pytest.mark.parametrize(
    "value", ["http://player.example", "https://10.0.0.1", "https://localhost"]
)
def test_installing_a_bad_host_is_refused_and_installs_nothing(value: str) -> None:
    with pytest.raises(PlayerUrlRefused):
        install_player_allowlist(value)
    assert player_allowlist() is None
