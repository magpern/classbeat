"""The SwedeBeat gateway client (ADR 0035 section 1, ``classbeat.egress_gateway``): one exact origin
(scheme, host, port), no redirect, no default header, ``trust_env=False``, a clamped total
deadline, on no urllib list, and refused until the origin is installed. Every request goes to an
``httpx.MockTransport`` (the netguard holds)."""

import asyncio
import gzip
import urllib.request
from collections.abc import AsyncIterator, Iterator

import httpx
import pytest

from classbeat import egress, egress_gateway
from classbeat.egress import WEB_URLLIB_HOSTS, WORKER_HOSTS, OutboundBlocked, RequestDeadline
from classbeat.egress_gateway import (
    LOGIN_SECONDS,
    MATCH_SECONDS,
    TOTAL_MAX_SECONDS,
    TOTAL_MIN_SECONDS,
    clamp_seconds,
    install_swedebeat_gateway_origin,
    swedebeat_gateway_async_client,
    swedebeat_gateway_origin,
    swedebeat_gateway_test_client,
)
from classbeat.swedebeat_gateway.answer import read_capped
from classbeat.swedebeat_gateway.url_policy import GatewayOrigin, GatewayUrlRefused

LAN = "http://192.168.1.222:7355"
NAMED = "https://sb.classbeat.eu"


@pytest.fixture(autouse=True)
def no_origin() -> Iterator[None]:
    install_swedebeat_gateway_origin(None)
    yield
    install_swedebeat_gateway_origin(None)


def answering(*responses: httpx.Response) -> tuple[httpx.MockTransport, list[httpx.Request]]:
    seen: list[httpx.Request] = []
    queue = list(responses)

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return queue.pop(0)

    return httpx.MockTransport(handler), seen


@pytest.mark.parametrize(
    ("origin", "url"),
    [
        (LAN, "http://192.168.1.222:7355/auth/login"),
        (NAMED, "https://sb.classbeat.eu/auth/login"),
        ("https://sb.classbeat.eu:8443", "https://sb.classbeat.eu:8443/auth/login"),
        ("http://[fc00::1]:7355", "http://[fc00::1]:7355/auth/login"),
    ],
)
async def test_the_client_reaches_its_origin_with_only_the_call_sites_headers(
    origin: str, url: str
) -> None:
    transport, seen = answering(httpx.Response(200, json={}))
    async with swedebeat_gateway_test_client(origin, transport) as client:
        answer = await client.post(
            "/auth/login", content=b"{}", headers={"X-API-Key": "k", "Accept": "application/json"}
        )
    assert answer.status_code == 200
    [request] = seen
    assert str(request.url) == url
    # Only what the call site set, plus what HTTP itself needs (host, length).
    assert set(request.headers) == {"host", "x-api-key", "accept", "content-length"}
    assert client.headers == httpx.Headers()
    assert not client.follow_redirects
    assert client.trust_env is False


@pytest.mark.parametrize(
    "url",
    [
        "http://192.168.1.223:7355/auth/login",  # another host
        "http://192.168.1.222:7356/auth/login",  # another port
        "http://192.168.1.222/auth/login",  # the default port, not the configured one
        "https://192.168.1.222:7355/auth/login",  # another scheme
        "http://evil.example:7355/auth/login",
        "http://user:pw@192.168.1.222:7355/auth/login",  # userinfo
        "http://127.0.0.1:7355/auth/login",
    ],
)
async def test_another_scheme_host_or_port_is_refused_before_anything_is_sent(url: str) -> None:
    transport, seen = answering(httpx.Response(200))
    async with swedebeat_gateway_test_client(LAN, transport) as client:
        with pytest.raises(OutboundBlocked) as refused:
            await client.get(url)
    assert seen == []
    assert refused.value.after_request is False


@pytest.mark.parametrize(
    "url", ["http://sb.classbeat.eu/x", "https://sb.classbeat.eu:8443/x", "https://other.eu/x"]
)
async def test_an_https_origin_is_exact_too(url: str) -> None:
    transport, seen = answering(httpx.Response(200))
    async with swedebeat_gateway_test_client(NAMED, transport) as client:
        assert (await client.get("https://sb.classbeat.eu:443/x")).status_code == 200
        with pytest.raises(OutboundBlocked):
            await client.get(url)
    assert len(seen) == 1


@pytest.mark.parametrize("status", [301, 302, 303, 307, 308])
@pytest.mark.parametrize(
    "location",
    [
        "http://evil.example/",
        "https://192.168.1.222:7355/",  # same host, another scheme
        "http://192.168.1.222:7356/",  # same host, another port
        "//evil.example/x",
        "http://[::1",  # not a URL
    ],
)
async def test_a_redirect_to_another_origin_is_refused_after_the_request(
    status: int, location: str
) -> None:
    transport, seen = answering(httpx.Response(status, headers={"location": location}))
    async with swedebeat_gateway_test_client(LAN, transport) as client:
        with pytest.raises(OutboundBlocked) as refused:
            await client.post("/auth/login")
    assert len(seen) == 1  # sent once, never followed
    assert refused.value.after_request is True


@pytest.mark.parametrize("status", [301, 302, 307, 308])
async def test_a_same_origin_redirect_comes_back_as_an_answer_and_is_not_followed(
    status: int,
) -> None:
    transport, seen = answering(httpx.Response(status, headers={"location": "/auth/session"}))
    async with swedebeat_gateway_test_client(LAN, transport) as client:
        answer = await client.post("/auth/login")
    assert answer.status_code == status  # for the call site to refuse
    assert len(seen) == 1


class Trickle(httpx.AsyncByteStream):
    def __init__(self, chunks: int, pause: float) -> None:
        self.chunks, self.pause = chunks, pause

    async def __aiter__(self) -> AsyncIterator[bytes]:
        for _ in range(self.chunks):
            await asyncio.sleep(self.pause)
            yield b"x"


async def test_a_trickling_body_ends_at_the_total_deadline() -> None:
    transport, _ = answering(httpx.Response(200, stream=Trickle(chunks=100, pause=0.05)))
    async with swedebeat_gateway_test_client(LAN, transport, total_seconds=1) as client:
        started = asyncio.get_running_loop().time()
        with pytest.raises(RequestDeadline):
            await client.get("/auth/session")
        assert asyncio.get_running_loop().time() - started < 2.5


async def test_a_slow_answer_ends_at_the_total_deadline() -> None:
    async def slow(request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(5)
        return httpx.Response(200)

    async with swedebeat_gateway_test_client(
        LAN, httpx.MockTransport(slow), total_seconds=0.01
    ) as client:  # clamped to the 1 s minimum
        started = asyncio.get_running_loop().time()
        with pytest.raises(RequestDeadline):
            await client.get("/auth/session")
        elapsed = asyncio.get_running_loop().time() - started
    assert 0.9 < elapsed < 2.5


@pytest.mark.parametrize(
    ("given", "clamped"),
    [(0.0, 1.0), (-5, 1.0), (0.5, 1.0), (30, 30.0), (150, 150.0), (151, 150.0), (1e9, 150.0)],
)
def test_the_deadline_is_clamped_to_1_to_150_seconds(given: float, clamped: float) -> None:
    assert clamp_seconds(given) == clamped
    assert (TOTAL_MIN_SECONDS, TOTAL_MAX_SECONDS) == (1.0, 150.0)
    assert LOGIN_SECONDS == 30.0
    assert MATCH_SECONDS == 150.0  # the contract asks for at least 120


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), True, "30", None])
def test_a_deadline_that_is_no_finite_number_is_refused(bad: object) -> None:
    with pytest.raises(ValueError, match="gateway deadline"):
        clamp_seconds(bad)  # pyright: ignore[reportArgumentType]


async def test_the_client_refuses_until_the_origin_is_installed() -> None:
    assert swedebeat_gateway_origin() is None
    with pytest.raises(OutboundBlocked):
        swedebeat_gateway_async_client(LOGIN_SECONDS)


async def test_the_installed_client_reaches_exactly_the_installed_origin(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    transport, seen = answering(httpx.Response(200))
    monkeypatch.setattr(egress_gateway, "gateway_network_transport", lambda: transport)
    install_swedebeat_gateway_origin(LAN)
    assert swedebeat_gateway_origin() == GatewayOrigin("http", "192.168.1.222", 7355)
    async with swedebeat_gateway_async_client(MATCH_SECONDS) as client:
        assert client.timeout.read == MATCH_SECONDS
        await client.get("/auth/session")
        with pytest.raises(OutboundBlocked):
            await client.get("http://10.0.0.1:7355/auth/session")
    assert [str(r.url) for r in seen] == ["http://192.168.1.222:7355/auth/session"]
    install_swedebeat_gateway_origin(None)
    with pytest.raises(OutboundBlocked):
        swedebeat_gateway_async_client(LOGIN_SECONDS)


def test_installing_a_bad_origin_is_refused_by_the_policy() -> None:
    with pytest.raises(GatewayUrlRefused):
        install_swedebeat_gateway_origin("http://gateway.example:7355")
    assert swedebeat_gateway_origin() is None


async def test_no_proxy_from_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.invalid:3128")
    monkeypatch.setenv("HTTP_PROXY", "http://proxy.invalid:3128")
    monkeypatch.setenv("ALL_PROXY", "http://proxy.invalid:3128")
    install_swedebeat_gateway_origin(LAN)
    async with swedebeat_gateway_async_client(LOGIN_SECONDS) as client:
        assert client._mounts == {}  # pyright: ignore[reportPrivateUsage]
        assert isinstance(client._transport, egress_gateway._GatewayTransport)  # pyright: ignore[reportPrivateUsage]


@pytest.mark.parametrize("host", ["192.168.1.222", "sb.classbeat.eu", "gateway.classbeat-e2e.test"])
def test_the_gateway_is_on_no_urllib_list(host: str) -> None:
    assert host not in WEB_URLLIB_HOSTS
    assert host not in WORKER_HOSTS
    assert host not in egress.worker_hosts(("anthropic", "openai", "deepseek"))


# --- The answer reader: raw, identity only, capped (classbeat.swedebeat_gateway.answer) ------


class Counted(httpx.AsyncByteStream):
    """A body of ``chunks`` chunks of ``size`` bytes, without a declared length."""

    def __init__(self, chunks: int, size: int) -> None:
        self.chunks, self.size, self.sent = chunks, size, 0

    async def __aiter__(self) -> AsyncIterator[bytes]:
        for _ in range(self.chunks):
            self.sent += 1
            yield b"x" * self.size


class Raw(httpx.AsyncByteStream):
    def __init__(self, body: bytes) -> None:
        self.body = body

    async def __aiter__(self) -> AsyncIterator[bytes]:
        yield self.body


async def _read(response: httpx.Response, cap: int) -> bytes | None:
    transport, _ = answering(response)
    client = swedebeat_gateway_test_client(LAN, transport)
    async with client, client.stream("GET", "/auth/session") as answer:
        return await read_capped(answer, cap)


async def test_an_answer_within_the_cap_is_read_raw() -> None:
    assert await _read(httpx.Response(200, content=b"x" * 16), 16) == b"x" * 16
    assert await _read(httpx.Response(200, stream=Counted(4, 4)), 16) == b"x" * 16


async def test_an_oversize_answer_is_refused_declared_or_not() -> None:
    assert await _read(httpx.Response(200, content=b"x" * 17), 16) is None
    body = Counted(1000, 1024)
    assert await _read(httpx.Response(200, stream=body), 4096) is None
    assert body.sent <= 5  # the rest of an oversized body is not read


@pytest.mark.parametrize("length", ["99999999999999999999", "-1", "1e3", "abc", ""])
async def test_an_odd_declared_length_is_refused(length: str) -> None:
    response = httpx.Response(200, headers={"content-length": length}, stream=Counted(1, 4))
    assert await _read(response, 16) is None


@pytest.mark.parametrize("encoding", ["gzip", "br", "deflate", "zstd", "gzip, identity", "x"])
async def test_an_encoded_answer_is_refused_and_never_decoded(encoding: str) -> None:
    bomb = gzip.compress(b"{" + b" " * 1_000_000 + b"}")  # small compressed, large decoded
    # A stream, so that not even the mock transport decodes it.
    response = httpx.Response(200, headers={"content-encoding": encoding}, stream=Raw(bomb))
    assert await _read(response, 64 * 1024) is None


async def test_an_identity_encoding_is_read() -> None:
    response = httpx.Response(200, headers={"content-encoding": "identity"}, content=b"{}")
    assert await _read(response, 16) == b"{}"


# --- Both entry points install the checked origin -------------------------------------------


@pytest.mark.usefixtures("restore_logging")
def test_the_web_entry_point_installs_the_origin(monkeypatch: pytest.MonkeyPatch) -> None:
    from classbeat.__main__ import Server, main

    monkeypatch.setattr(Server, "run", lambda self, sockets=None: None)
    monkeypatch.setattr(urllib.request, "_opener", None)
    monkeypatch.setenv("CLASSBEAT_SWEDEBEAT_GATEWAY_URL", "http://192.168.1.222:7355/")
    assert main([]) == 0
    assert swedebeat_gateway_origin() == GatewayOrigin("http", "192.168.1.222", 7355)


@pytest.mark.usefixtures("restore_logging")
def test_the_worker_entry_point_installs_the_origin(monkeypatch: pytest.MonkeyPatch) -> None:
    from classbeat.worker import __main__ as worker_main

    async def no_run(_settings: object, _signer: object = None) -> None:
        return None

    monkeypatch.setattr(urllib.request, "_opener", None)
    monkeypatch.setattr(worker_main, "_run", no_run)
    monkeypatch.setattr(worker_main, "sweep_work_root", lambda: None)
    monkeypatch.setenv("CLASSBEAT_MODEL_PROVIDER", "fake")
    monkeypatch.setenv("CLASSBEAT_SWEDEBEAT_GATEWAY_URL", "https://sb.classbeat.eu")
    assert worker_main.main() == 0
    assert swedebeat_gateway_origin() == GatewayOrigin("https", "sb.classbeat.eu", 443)
    monkeypatch.delenv("CLASSBEAT_SWEDEBEAT_GATEWAY_URL")
    assert worker_main.main() == 0
    assert swedebeat_gateway_origin() is None  # unset: the client refuses
