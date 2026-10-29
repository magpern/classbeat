"""The worker's outbound allowlist (ADR 0006 §9). No test opens a connection: the network
handler (urllib) and transport (httpx) are replaced by fakes that record what reached them."""

import io
import logging
import ssl
import urllib.error
import urllib.request
import urllib.response
from email.message import Message
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import certifi
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
    assert raised.value.__cause__ is None
    assert raised.value.__context__ is None  # no ValueError holding URL parts
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


@pytest.mark.parametrize(
    ("start", "target"),
    [
        ("https://api.mowl.com/v1/start", "https://api.anthropic.com/v1/messages"),
        ("https://api.anthropic.com/v1/x", "https://api.mowl.com/v1/programs"),
    ],
)
def test_a_redirect_to_the_other_allowlisted_host_is_refused(start: str, target: str) -> None:
    """urllib copies the library's authorization header onto the next hop: never another host."""
    fake = FakeHTTPS(redirects={start: target})
    request = urllib.request.Request(start, headers={"authorization": "planted-mowl-token"})  # noqa: S310
    with pytest.raises(OutboundBlocked) as raised:
        _opener(fake).open(request, timeout=1)
    assert raised.value.host == urlsplit(target).hostname
    assert [u for u, _, _ in fake.seen] == [start]


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


class Mapped(Exception):
    pass


@pytest.mark.parametrize("url", ["https://evil.example/x", "https://api.anthropic.com/v1/x"])
async def test_refused_maps_a_refusal_with_the_refusal_as_its_cause(url: str) -> None:
    recorder = Recorder(redirect_to="https://evil.example/steal")
    client = allowlisted_async_client(
        inner=httpx.MockTransport(recorder), refused=lambda _: Mapped()
    )
    async with client:
        with pytest.raises(Mapped) as raised:
            await client.get(url)
    assert isinstance(raised.value.__cause__, OutboundBlocked)
    assert raised.value.__cause__.host == "evil.example"


async def test_the_model_client_checks_every_redirect_hop() -> None:
    recorder = Recorder(redirect_to="https://evil.example/steal")
    async with allowlisted_async_client(inner=httpx.MockTransport(recorder)) as client:
        with pytest.raises(OutboundBlocked):
            await client.get("https://api.anthropic.com/v1/x", follow_redirects=True)
    assert recorder.seen == ["https://api.anthropic.com/v1/x"]


@pytest.mark.parametrize("follow", [True, False])
async def test_the_model_client_refuses_a_redirect_to_the_other_allowlisted_host(
    follow: bool,
) -> None:
    """httpx keeps ``x-api-key`` on a cross-origin hop: the transport refuses the redirect."""
    recorder = Recorder(redirect_to="https://api.mowl.com/v1/programs")
    async with allowlisted_async_client(inner=httpx.MockTransport(recorder)) as client:
        with pytest.raises(OutboundBlocked) as raised:
            await client.get(
                "https://api.anthropic.com/v1/x",
                headers={"x-api-key": "planted"},
                follow_redirects=follow,
            )
    assert raised.value.host == "api.mowl.com"
    assert recorder.seen == ["https://api.anthropic.com/v1/x"]


async def test_the_model_client_follows_a_same_host_redirect() -> None:
    recorder = Recorder(redirect_to="/v1/next")
    async with allowlisted_async_client(inner=httpx.MockTransport(recorder)) as client:
        response = await client.get("https://api.anthropic.com/v1/x", follow_redirects=True)
    assert response.status_code == 200
    assert recorder.seen == ["https://api.anthropic.com/v1/x", "https://api.anthropic.com/v1/next"]


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


# --- TLS key log (SSLKEYLOGFILE) -------------------------------------------------------------


def _assert_no_key_log(context: ssl.SSLContext) -> None:
    assert context.keylog_filename is None
    assert context.verify_mode == ssl.CERT_REQUIRED
    assert context.check_hostname is True


@pytest.fixture
def key_log(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """``SSLKEYLOGFILE`` set; the stdlib's default context would write it (checked here)."""
    path = tmp_path / "keys.log"
    monkeypatch.setenv("SSLKEYLOGFILE", str(path))
    assert ssl.create_default_context().keylog_filename == str(path)  # the risk is real
    path.unlink()
    return path


def test_the_tls_context_ignores_sslkeylogfile(key_log: Path) -> None:
    _assert_no_key_log(egress.tls_client_context())
    _assert_no_key_log(egress.tls_client_context(cafile=certifi.where()))
    assert not key_log.exists()


def test_the_urllib_opener_writes_no_tls_key_log(key_log: Path) -> None:
    opener = build_allowlist_opener(WORKER_ALLOWLIST)
    [https] = [h for h in _field(opener, "handlers") if isinstance(h, urllib.request.HTTPSHandler)]
    _assert_no_key_log(_field(https, "_context"))
    assert not key_log.exists()


def test_the_model_client_writes_no_tls_key_log(key_log: Path) -> None:
    client = allowlisted_async_client()  # built only, never used: nothing to close
    transport = client._transport  # pyright: ignore[reportPrivateUsage]
    assert isinstance(transport, egress.AllowlistTransport)
    network = _field(transport, "_inner")
    assert isinstance(network, httpx.AsyncHTTPTransport)
    _assert_no_key_log(_field(_field(network, "_pool"), "_ssl_context"))
    assert not key_log.exists()


# --- the model transport's trust store ------------------------------------------------------


def test_the_model_transport_trusts_certifi_not_the_system_store(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    asked: list[str | None] = []
    real = egress.tls_client_context

    def spy(*, cafile: str | None = None) -> ssl.SSLContext:
        asked.append(cafile)
        return real(cafile=cafile)

    monkeypatch.setattr(egress, "tls_client_context", spy)
    network = egress.model_network_transport()
    assert asked == [certifi.where()]  # switching to the system store (None) fails here
    context: ssl.SSLContext = _field(_field(network, "_pool"), "_ssl_context")
    expected = real(cafile=certifi.where()).get_ca_certs()
    assert expected  # the bundle is not empty
    assert context.get_ca_certs() == expected


def test_certifi_is_a_declared_dependency() -> None:
    """``classbeat.egress`` imports it, so it must not rest on being pulled in by httpx."""
    import tomllib

    pyproject = Path(__file__).resolve().parents[1] / "pyproject.toml"
    declared = tomllib.loads(pyproject.read_text("utf-8"))["project"]["dependencies"]
    assert any(d.split(">")[0].split("=")[0].split("[")[0].strip() == "certifi" for d in declared)
