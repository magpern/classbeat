"""The spotdown client builder (ADR 0040 section 10, ``classbeat.egress_spotdown``): one exact
origin and the service key, installed together or not at all; the key set by the transport only
after the origin check; no redirect, no default header, ``trust_env=False``, size caps and a total
deadline; on no urllib list. Every request goes to an ``httpx.MockTransport`` (netguard holds)."""

import asyncio
import copy
import logging
import pickle
from collections.abc import AsyncIterator, Iterator

import httpx
import pytest
from pydantic import SecretStr

from classbeat import egress_spotdown
from classbeat.egress import WEB_URLLIB_HOSTS, WORKER_HOSTS, AnswerTooLarge, OutboundBlocked
from classbeat.egress_deadline import RequestDeadline
from classbeat.egress_spotdown import (
    ANSWER_MAX,
    REQUEST_MAX,
    TOTAL_MAX_SECONDS,
    TOTAL_MIN_SECONDS,
    UPLOAD_SECONDS,
    BodyTooLarge,
    clamp_seconds,
    install_spotdown,
    spotdown_async_client,
    spotdown_installed,
    spotdown_test_client,
    spotdown_upload_client,
)
from classbeat.spotdown.key_policy import REDACTED, ServiceKey
from classbeat.spotdown.url_policy import SpotdownUrlRefused

ORIGIN = "http://spotdown-api:8000"
KEY = SecretStr("test-spotdown-key-for-the-builder-Jq3Wx8Zr5")


@pytest.fixture(autouse=True)
def nothing_installed() -> Iterator[None]:
    install_spotdown(None, None)
    yield
    install_spotdown(None, None)


def answering(*responses: httpx.Response) -> tuple[httpx.MockTransport, list[httpx.Request]]:
    seen: list[httpx.Request] = []
    queue = list(responses)

    def handler(request: httpx.Request) -> httpx.Response:
        request.read()
        seen.append(request)
        return queue.pop(0)

    return httpx.MockTransport(handler), seen


@pytest.mark.parametrize(
    ("url", "key"),
    [(None, KEY), ("", KEY), (ORIGIN, None), (ORIGIN, SecretStr("")), (None, None)],
)
def test_without_both_the_origin_and_the_key_nothing_can_be_built(
    url: str | None, key: SecretStr | None
) -> None:
    install_spotdown(url, key)
    assert not spotdown_installed()
    with pytest.raises(OutboundBlocked):
        spotdown_async_client(10)
    with pytest.raises(OutboundBlocked):
        spotdown_upload_client()


def test_install_checks_the_origin_again() -> None:
    with pytest.raises(SpotdownUrlRefused):
        install_spotdown("https://spotdown.example.com:443", KEY)
    assert not spotdown_installed()


async def test_the_production_client_pins_the_installed_origin() -> None:
    install_spotdown(ORIGIN, KEY)
    async with spotdown_async_client(10) as client:
        assert str(client.base_url) == "http://spotdown-api:8000"
        assert client.follow_redirects is False
        assert client.headers == {}
        assert client.timeout.read == 10
        assert client._trust_env is False  # pyright: ignore[reportPrivateUsage]
    async with spotdown_upload_client() as upload:
        assert upload.timeout.read == UPLOAD_SECONDS


@pytest.mark.parametrize(
    "url",
    [
        "http://spotdown-api:8001/v2/jobs/x",  # another port
        "https://spotdown-api:8000/v2/jobs/x",  # another scheme
        "http://spotdown-worker:8000/v2/jobs/x",  # another host
        "http://user@spotdown-api:8000/v2/jobs/x",  # userinfo
        "http://api.example.com/v2/jobs/x",
    ],
)
async def test_another_origin_is_refused_before_the_key_is_set(url: str) -> None:
    transport, seen = answering(httpx.Response(200))
    async with spotdown_test_client(ORIGIN, KEY, transport) as client:
        with pytest.raises(OutboundBlocked) as refused:
            await client.get(url)
    assert seen == []
    assert not refused.value.after_request


async def test_the_key_is_set_by_the_transport_and_replaces_a_callers_value() -> None:
    transport, seen = answering(httpx.Response(200, json={}))
    async with spotdown_test_client(ORIGIN, KEY, transport) as client:
        await client.get("/v2/jobs/job_1", headers={"Authorization": "Bearer something-else"})
    (request,) = seen
    assert request.headers.get_list("authorization") == [f"Bearer {KEY.get_secret_value()}"]
    assert KEY.get_secret_value() not in str(request.url)
    assert set(request.headers) <= {"authorization", "host"}


@pytest.mark.parametrize("status", [301, 302, 303, 307, 308, 300, 304])
async def test_every_redirect_is_refused_after_the_request(status: int) -> None:
    transport, _ = answering(httpx.Response(status, headers={"location": "/v2/jobs/job_2"}))
    async with spotdown_test_client(ORIGIN, KEY, transport) as client:
        with pytest.raises(OutboundBlocked) as refused:
            await client.get("/v2/jobs/job_1")
    assert refused.value.after_request


async def test_a_declared_answer_over_the_cap_is_refused() -> None:
    big = httpx.Response(200, headers={"content-length": str(ANSWER_MAX + 1)}, content=b"")
    transport, _ = answering(big)
    async with spotdown_test_client(ORIGIN, KEY, transport) as client:
        with pytest.raises(AnswerTooLarge):
            await client.get("/v2/analyses/an_1")


@pytest.mark.parametrize("declared", ["\u0661", "\u00b2", "\uff11", "1 ", "-1", ""])
async def test_a_declared_answer_length_that_is_not_ascii_digits_is_refused(declared: str) -> None:
    # "\u0661" (Arabic-Indic one) passes str.isdigit and int() reads it as 1: under the cap.
    odd = httpx.Response(200, headers=[(b"content-length", declared.encode())], content=b"")
    transport, _ = answering(odd)
    async with spotdown_test_client(ORIGIN, KEY, transport) as client:
        with pytest.raises(AnswerTooLarge):
            await client.get("/v2/analyses/an_1")


@pytest.mark.parametrize("declared", ["\u0661", "\u00b2", "\uff11"])
async def test_a_declared_request_length_that_is_not_ascii_digits_is_refused(declared: str) -> None:
    transport, seen = answering(httpx.Response(200))
    async with spotdown_test_client(ORIGIN, KEY, transport) as client:
        declared_header = httpx.Headers([(b"content-length", declared.encode())])
        request = client.build_request("POST", "/v2/match", content=b"x", headers=declared_header)
        assert request.headers["content-length"] == declared
        with pytest.raises(BodyTooLarge):
            await client.send(request)
    assert seen == []


async def test_a_streamed_answer_over_the_cap_is_cut_off() -> None:
    async def body() -> AsyncIterator[bytes]:
        for _ in range(ANSWER_MAX // 65536 + 2):
            yield b"x" * 65536

    transport, _ = answering(httpx.Response(200, content=body()))
    async with spotdown_test_client(ORIGIN, KEY, transport) as client:
        with pytest.raises(AnswerTooLarge):
            await client.get("/v2/analyses/an_1")


async def test_a_request_body_over_its_cap_is_cut_off() -> None:
    transport, seen = answering(httpx.Response(200))
    async with spotdown_test_client(ORIGIN, KEY, transport) as client:
        with pytest.raises(BodyTooLarge):
            await client.post("/v2/match", content=b"x" * (REQUEST_MAX + 1))
    assert seen == []


async def test_a_trickling_answer_ends_at_the_deadline() -> None:
    async def body() -> AsyncIterator[bytes]:
        yield b"{"
        await asyncio.sleep(5)
        yield b"}"

    transport, _ = answering(httpx.Response(200, content=body()))
    async with spotdown_test_client(ORIGIN, KEY, transport, total_seconds=1) as client:
        with pytest.raises(RequestDeadline):
            await client.get("/v2/jobs/job_1")


@pytest.mark.parametrize(
    ("given", "expected"),
    [(0, TOTAL_MIN_SECONDS), (10, 10.0), (10_000, TOTAL_MAX_SECONDS)],
)
def test_the_deadline_is_clamped(given: float, expected: float) -> None:
    assert clamp_seconds(given) == expected


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), True, "10"])
def test_a_deadline_that_is_not_a_finite_number_is_refused(bad: object) -> None:
    with pytest.raises(ValueError, match="spotdown deadline"):
        clamp_seconds(bad)  # pyright: ignore[reportArgumentType]


def test_the_origin_is_on_no_urllib_list() -> None:
    install_spotdown(ORIGIN, KEY)
    assert not any("spotdown" in h for h in WORKER_HOSTS | WEB_URLLIB_HOSTS)


def test_the_service_key_holder_never_shows_the_key(caplog: pytest.LogCaptureFixture) -> None:
    held = ServiceKey(KEY)
    secret = KEY.get_secret_value()
    assert repr(held) == str(held) == f"{held}" == f"{held!r}" == REDACTED
    assert secret not in repr([held, {"k": held}])
    with pytest.raises(TypeError):
        pickle.dumps(held)
    with pytest.raises(TypeError):
        copy.copy(held)
    with caplog.at_level(logging.DEBUG):
        logging.getLogger("test").warning("key %s %r", held, held)
        install_spotdown(ORIGIN, KEY)
    assert secret not in caplog.text
    assert secret not in repr(egress_spotdown._installed)  # pyright: ignore[reportPrivateUsage]


async def test_a_streamed_request_body_over_its_cap_is_cut_off() -> None:
    sent: list[int] = []

    async def body() -> AsyncIterator[bytes]:
        for _ in range(3):
            sent.append(1)
            yield b"x" * (REQUEST_MAX // 2)

    async def handler(request: httpx.Request) -> httpx.Response:
        async for _ in request.stream:  # pyright: ignore[reportGeneralTypeIssues]
            pass
        return httpx.Response(200)

    async with spotdown_test_client(ORIGIN, KEY, httpx.MockTransport(handler)) as client:
        with pytest.raises(BodyTooLarge):
            await client.post("/v2/match", content=body())
    assert len(sent) == 3  # the third chunk passes the cap and is not sent
