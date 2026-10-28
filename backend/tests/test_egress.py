"""The worker's outbound allowlist (ADR 0006 §9). No test opens a connection: the network
handler (urllib) and transport (httpx) are replaced by fakes that record what reached them."""

import io
import logging
import urllib.error
import urllib.request
import urllib.response
from email.message import Message
from typing import Any

import httpx
import pytest

from classbeat import egress
from classbeat.egress import (
    WORKER_ALLOWLIST,
    Allowlist,
    OutboundBlocked,
    allowlisted_async_client,
    build_allowlist_opener,
    install_worker_allowlist,
)

PROXY_VARIABLES = ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy", "ALL_PROXY")


def _field(obj: object, name: str) -> Any:
    """A stdlib attribute the type stubs leave out."""
    return getattr(obj, name)


class FakeHTTPS(urllib.request.BaseHandler):
    """Answers ``https`` without a network: 200, or a redirect from ``redirects`` by URL."""

    def __init__(self, redirects: dict[str, str] | None = None, status: int = 200) -> None:
        self.seen: list[tuple[str, str, bool]] = []
        self.redirects = redirects or {}
        self.status = status

    def https_open(self, req: urllib.request.Request) -> Any:
        self.seen.append((req.full_url, req.host, req.has_proxy()))
        headers = Message()
        status = self.status
        if req.full_url in self.redirects:
            status = 302
            headers["Location"] = self.redirects[req.full_url]
        response = urllib.response.addinfourl(
            io.BytesIO(b'{"Data": 1}'), headers, req.full_url, status
        )
        response.msg = "fake"  # pyright: ignore[reportAttributeAccessIssue]
        return response


def _opener(fake: FakeHTTPS) -> urllib.request.OpenerDirector:
    return build_allowlist_opener(WORKER_ALLOWLIST, https_handler=fake)


@pytest.mark.parametrize(
    "url",
    [
        "https://api.mowl.com/v1/programs",
        "https://API.MOWL.COM/v1/programs",
        "https://api.mowl.com:443/v1/programs",
        "https://api.anthropic.com/v1/messages",
    ],
)
def test_https_to_an_allowlisted_host_goes_out(url: str) -> None:
    fake = FakeHTTPS()
    with _opener(fake).open(url, timeout=1) as response:
        assert response.read() == b'{"Data": 1}'
    assert [u for u, _, _ in fake.seen] == [url]


BLOCKED = [
    ("http://api.mowl.com/v1/programs", "api.mowl.com"),
    ("https://api.mowl.com:8443/v1/programs", "api.mowl.com"),
    ("https://evil.example/v1", "evil.example"),
    ("https://api.mowl.com.evil.example/", "api.mowl.com.evil.example"),
    ("https://api.mowl.com./v1", "api.mowl.com."),
    ("https://user:pw@api.mowl.com/v1", "api.mowl.com"),
    ("https://api.mowl.com@evil.example/v1", "evil.example"),
    ("https://127.0.0.1/", "127.0.0.1"),
    ("https://[::1]/", "::1"),
    ("https://169.254.169.254/latest/meta-data", "169.254.169.254"),
    ("ftp://api.mowl.com/x", "api.mowl.com"),
    ("file:///etc/passwd", ""),
    ("data:text/plain,hello", ""),
    ("https://api.mowl.com:99999/", ""),
    ("https://api.spotify.com/v1/me", "api.spotify.com"),  # M4 adds it, not before
]


@pytest.mark.parametrize(("url", "host"), BLOCKED)
def test_anything_else_is_refused_before_a_connection(url: str, host: str) -> None:
    fake = FakeHTTPS()
    with pytest.raises(OutboundBlocked) as raised:
        _opener(fake).open(url, timeout=1)
    assert raised.value.host == host
    assert raised.value.args == ("outbound request blocked",)
    assert fake.seen == []


def test_a_refusal_logs_the_host_only(caplog: pytest.LogCaptureFixture) -> None:
    url = "https://evil.example/path/secret-path?token=planted-query#frag"
    opener = _opener(FakeHTTPS())
    with caplog.at_level(logging.WARNING, "classbeat.egress"), pytest.raises(OutboundBlocked):
        opener.open(url, timeout=1)
    [record] = caplog.records
    assert record.getMessage() == "outbound request blocked"
    assert record.__dict__["host"] == "evil.example"
    assert "secret-path" not in repr(record.__dict__)
    assert "planted-query" not in repr(record.__dict__)


def test_a_logged_host_is_printable_and_bounded() -> None:
    with pytest.raises(OutboundBlocked) as raised:
        WORKER_ALLOWLIST.check("https", "EVIL\n.example" + "a" * 400, None, False)
    assert raised.value.host.startswith("evil?.example")
    assert len(raised.value.host) == 253


@pytest.mark.parametrize(
    "target",
    [
        "https://evil.example/steal",
        "http://api.mowl.com/v1/x",
        "https://api.mowl.com:8443/v1/x",
        "ftp://api.mowl.com/x",
    ],
)
def test_every_redirect_hop_is_checked(target: str) -> None:
    start = "https://api.mowl.com/v1/start"
    fake = FakeHTTPS(redirects={start: target})
    with pytest.raises(OutboundBlocked):
        _opener(fake).open(start, timeout=1)
    assert [u for u, _, _ in fake.seen] == [start]  # the hop never reached the network handler


def test_a_redirect_to_an_allowlisted_host_is_followed() -> None:
    start = "https://api.mowl.com/v1/start"
    target = "https://api.mowl.com/v1/next"
    fake = FakeHTTPS(redirects={start: target})
    with _opener(fake).open(start, timeout=1) as response:
        assert response.geturl() == target
    assert [u for u, _, _ in fake.seen] == [start, target]


@pytest.mark.parametrize("variable", PROXY_VARIABLES)
def test_a_proxy_in_the_environment_is_ignored(
    variable: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(variable, "http://proxy.evil.example:3128")
    monkeypatch.setenv("NO_PROXY", "")
    fake = FakeHTTPS()
    with _opener(fake).open("https://api.mowl.com/v1/x", timeout=1):
        pass
    assert fake.seen == [("https://api.mowl.com/v1/x", "api.mowl.com", False)]


def test_a_request_with_a_proxy_set_on_it_is_refused() -> None:
    fake = FakeHTTPS()
    request = urllib.request.Request("https://api.mowl.com/v1/x")
    request.set_proxy("proxy.evil.example:3128", "https")
    with pytest.raises(OutboundBlocked):
        _opener(fake).open(request, timeout=1)
    assert fake.seen == []


def test_http_errors_still_raise_http_error_as_the_library_expects() -> None:
    with pytest.raises(urllib.error.HTTPError) as raised:
        _opener(FakeHTTPS(status=401)).open("https://api.mowl.com/v1/x", timeout=1)
    assert raised.value.code == 401


def test_the_opener_has_no_handler_for_other_schemes() -> None:
    opener = build_allowlist_opener()
    schemes = set(_field(opener, "handle_open")) - {"default", "unknown"}
    assert schemes == {"https"}
    # The empty ProxyHandler has no ``<scheme>_open``, so nothing proxies (an environment proxy
    # would have registered ``https_open`` on a ProxyHandler).
    assert not any(isinstance(h, urllib.request.ProxyHandler) for h in _field(opener, "handlers"))
    [https] = _field(opener, "handle_open")["https"]
    assert not isinstance(https, urllib.request.ProxyHandler)


def test_the_real_https_handler_checks_again_before_connecting() -> None:
    opener = build_allowlist_opener()
    [network] = [
        h for h in _field(opener, "handlers") if isinstance(h, urllib.request.HTTPSHandler)
    ]
    with pytest.raises(OutboundBlocked):
        network.https_open(urllib.request.Request("https://evil.example/"))  # pyright: ignore[reportAttributeAccessIssue]


def test_install_makes_urlopen_use_the_allowlist(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(urllib.request, "_opener", None)  # restored after the test
    opener = install_worker_allowlist()
    assert _field(urllib.request, "_opener") is opener
    with pytest.raises(OutboundBlocked):
        urllib.request.urlopen("https://evil.example/", timeout=1)


def test_the_mowl_library_call_goes_through_the_installed_opener(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from mowl import api

    fake = FakeHTTPS()
    monkeypatch.setattr(urllib.request, "_opener", None)
    urllib.request.install_opener(build_allowlist_opener(https_handler=fake))
    assert api.call(None, "GET", "v1/ping") == 1
    assert fake.seen == [("https://api.mowl.com/v1/ping", "api.mowl.com", False)]
    monkeypatch.setattr(api, "API", "https://elsewhere.example/")
    with pytest.raises(OutboundBlocked):
        api.call(None, "GET", "v1/ping")


def test_the_worker_list_is_exactly_mowl_and_anthropic() -> None:
    assert frozenset({"api.mowl.com", "api.anthropic.com"}) == egress.WORKER_HOSTS


@pytest.mark.parametrize("host", ["API.example", "", "trailing.dot."])
def test_an_allowlist_takes_only_lower_case_names(host: str) -> None:
    with pytest.raises(ValueError, match="lower case"):
        Allowlist(frozenset({host}))


# --- httpx ---------------------------------------------------------------------------------


class Recorder:
    def __init__(self, redirect_to: str | None = None) -> None:
        self.seen: list[str] = []
        self.redirect_to = redirect_to

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.seen.append(str(request.url))
        if self.redirect_to is not None and len(self.seen) == 1:
            return httpx.Response(307, headers={"Location": self.redirect_to})
        return httpx.Response(200, json={"ok": True})


async def test_the_model_client_reaches_an_allowlisted_host() -> None:
    recorder = Recorder()
    async with allowlisted_async_client(inner=httpx.MockTransport(recorder)) as client:
        response = await client.post("https://api.anthropic.com/v1/messages", json={})
    assert response.status_code == 200
    assert recorder.seen == ["https://api.anthropic.com/v1/messages"]


@pytest.mark.parametrize(("url", "_host"), [b for b in BLOCKED if b[0].startswith("http")])
async def test_the_model_client_refuses_anything_else(url: str, _host: str) -> None:
    recorder = Recorder()
    async with allowlisted_async_client(inner=httpx.MockTransport(recorder)) as client:
        with pytest.raises((OutboundBlocked, httpx.InvalidURL)):
            await client.get(url)
    assert recorder.seen == []


async def test_the_model_client_checks_every_redirect_hop() -> None:
    recorder = Recorder(redirect_to="https://evil.example/steal")
    async with allowlisted_async_client(inner=httpx.MockTransport(recorder)) as client:
        with pytest.raises(OutboundBlocked):
            await client.get("https://api.anthropic.com/v1/x", follow_redirects=True)
    assert recorder.seen == ["https://api.anthropic.com/v1/x"]


@pytest.mark.parametrize("variable", PROXY_VARIABLES)
async def test_the_model_client_ignores_proxy_settings(
    variable: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(variable, "http://proxy.evil.example:3128")
    async with allowlisted_async_client() as client:
        assert client.trust_env is False
        assert client._mounts == {}  # pyright: ignore[reportPrivateUsage]
        transport = client._transport  # pyright: ignore[reportPrivateUsage]
        assert isinstance(transport, egress.AllowlistTransport)


async def test_the_model_client_has_the_adr_timeout() -> None:
    async with allowlisted_async_client() as client:
        assert client.timeout == httpx.Timeout(120.0)
