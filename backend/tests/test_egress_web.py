"""The web process's outbound allowlist and redirect policy (SR-03, #353; threat model T19).

No test opens a connection. The library's own ``mowl.api.call`` (urllib) runs against a fake
upstream that answers per host and records every request it receives, headers included: a
redirect from one fake host to a second must never show the MOWL token to the second."""

import io
import ssl
import urllib.request
import urllib.response
from collections.abc import Callable
from email.message import Message
from typing import Any
from urllib.parse import urlsplit

import httpx
import pytest
from mowl import api
from mowl.credentials import Credentials

from classbeat import __main__ as entry
from classbeat import egress
from classbeat.auth import oauth_client
from classbeat.auth.oauth_providers import Provider
from classbeat.egress import OutboundBlocked, install_web_allowlist

TOKEN = "11111111-2222-3333-4444-555555555555"  # a public test value, 36 characters
CREDS = Credentials(mowl_token=TOKEN)
START = "https://api.mowl.com/v1/ping"


class FakeUpstreams(urllib.request.BaseHandler):
    """Answers ``https`` for every host without a network and records what each host received.
    ``redirects`` maps a URL to the ``Location`` it answers with (302); else 200 with data."""

    def __init__(self, redirects: dict[str, str] | None = None) -> None:
        self.redirects = redirects or {}
        self.received: dict[str, list[dict[str, str]]] = {}

    def https_open(self, req: urllib.request.Request) -> Any:
        host = urlsplit(req.full_url).hostname or ""
        headers = {k.lower(): v for k, v in req.header_items()}
        self.received.setdefault(host, []).append({"url": req.full_url, **headers})
        out = Message()
        status = 200
        if req.full_url in self.redirects:
            status = 302
            out["Location"] = self.redirects[req.full_url]
        response = urllib.response.addinfourl(io.BytesIO(b'{"Data": 1}'), out, req.full_url, status)
        response.msg = "fake"  # pyright: ignore[reportAttributeAccessIssue]
        return response


@pytest.fixture
def web_opener(monkeypatch: pytest.MonkeyPatch) -> Callable[[FakeUpstreams], None]:
    """Installs the web's opener (as ``install_web_allowlist`` builds it) over a fake network."""

    def install(fake: FakeUpstreams) -> None:
        opener = egress.build_allowlist_opener(egress.WEB_URLLIB_ALLOWLIST, https_handler=fake)
        monkeypatch.setattr(urllib.request, "_opener", opener)

    return install


def test_the_web_urllib_list_is_exactly_mowl_and_the_spotify_api() -> None:
    """What the library reaches in the web process: MOWL, and Spotify's API for Check again."""
    assert frozenset({"api.mowl.com", "api.spotify.com"}) == egress.WEB_URLLIB_HOSTS
    assert egress.WEB_URLLIB_ALLOWLIST.hosts == egress.WEB_URLLIB_HOSTS


@pytest.mark.parametrize(
    "target",
    [
        "https://evil.example/steal",  # not on any list
        "https://api.spotify.com/v1/me",  # on the web's list too, but another host
        "https://accounts.spotify.com/api/token",  # on the worker's list, not the web's
        "https://api.anthropic.com/v1/messages",
        "http://api.mowl.com/v1/ping",  # same host, plain http
        "https://api.mowl.com:8443/v1/ping",  # same host, other port
        "https://user:pass@api.mowl.com/v1/ping",
        "ftp://api.mowl.com/x",
        "//evil.example/steal",  # scheme-relative
    ],
)
def test_a_redirect_never_shows_the_mowl_token_to_a_second_host(
    target: str, web_opener: Callable[[FakeUpstreams], None]
) -> None:
    fake = FakeUpstreams(redirects={START: target})
    web_opener(fake)
    with pytest.raises(OutboundBlocked) as raised:
        api.call(CREDS, "GET", "v1/ping")
    assert raised.value.after_request is True  # the first request had been sent
    assert list(fake.received) == ["api.mowl.com"]  # the second host received nothing at all
    assert [r["authorization"] for r in fake.received["api.mowl.com"]] == [TOKEN]


def test_a_spotify_redirect_never_shows_the_spotify_token_to_mowl(
    web_opener: Callable[[FakeUpstreams], None], monkeypatch: pytest.MonkeyPatch
) -> None:
    from mowl import spotify_web

    start = "https://api.spotify.com/v1/me"
    fake = FakeUpstreams(redirects={start: "https://api.mowl.com/v1/ping"})
    web_opener(fake)
    creds = Credentials(spotify_access_token="spotify-test-token", spotify_refresh_enabled=False)
    creds.spotify_expires_at = 4_000_000_000.0
    with pytest.raises(OutboundBlocked):
        spotify_web.me(creds)
    assert list(fake.received) == ["api.spotify.com"]


def test_the_token_is_on_the_wire_to_mowl_itself_so_the_checks_above_can_fail(
    web_opener: Callable[[FakeUpstreams], None],
) -> None:
    fake = FakeUpstreams()
    web_opener(fake)
    assert api.call(CREDS, "GET", "v1/ping") == 1
    assert [r["authorization"] for r in fake.received["api.mowl.com"]] == [TOKEN]


def test_a_same_host_https_redirect_is_followed_and_keeps_the_header(
    web_opener: Callable[[FakeUpstreams], None],
) -> None:
    """Decided and documented (``classbeat.egress``): the host the token was meant for."""
    next_url = "https://api.mowl.com/v1/next"
    fake = FakeUpstreams(redirects={START: next_url})
    web_opener(fake)
    assert api.call(CREDS, "GET", "v1/ping") == 1
    assert [r["url"] for r in fake.received["api.mowl.com"]] == [START, next_url]
    assert [r["authorization"] for r in fake.received["api.mowl.com"]] == [TOKEN, TOKEN]


def test_the_web_opener_refuses_an_unlisted_host_before_anything_is_sent(
    web_opener: Callable[[FakeUpstreams], None], monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = FakeUpstreams()
    web_opener(fake)
    monkeypatch.setattr(api, "API", "https://evil.example/")
    with pytest.raises(OutboundBlocked) as raised:
        api.call(CREDS, "GET", "v1/ping")
    assert (raised.value.host, raised.value.after_request) == ("evil.example", False)
    assert fake.received == {}


@pytest.mark.parametrize("variable", ["HTTPS_PROXY", "https_proxy", "ALL_PROXY"])
def test_a_proxy_in_the_environment_is_not_used(
    variable: str, monkeypatch: pytest.MonkeyPatch, web_opener: Callable[[FakeUpstreams], None]
) -> None:
    monkeypatch.setenv(variable, "http://proxy.evil.example:3128")
    fake = FakeUpstreams()
    web_opener(fake)
    assert api.call(CREDS, "GET", "v1/ping") == 1
    assert list(fake.received) == ["api.mowl.com"]


def test_installing_the_web_allowlist_sets_the_process_opener_and_not_the_worker_flag() -> None:
    opener = install_web_allowlist()
    assert getattr(urllib.request, "_opener") is opener  # noqa: B009
    assert not egress.worker_allowlist_installed()  # the web-only call sites keep working
    with pytest.raises(OutboundBlocked):
        urllib.request.urlopen("https://accounts.spotify.com/api/token", timeout=1)
    with pytest.raises(OutboundBlocked):
        urllib.request.urlopen("http://api.mowl.com/v1/ping", timeout=1)


def test_the_server_entry_point_installs_the_web_allowlist(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    started: list[object] = []

    class Stop:
        def __init__(self, config: object, app: object) -> None:
            started.append(getattr(urllib.request, "_opener"))  # noqa: B009

        def run(self) -> None:
            return None

    monkeypatch.setattr(entry, "create_app", lambda settings: object())
    monkeypatch.setattr(entry, "Server", Stop)
    monkeypatch.setattr(entry, "server_config", lambda app, host, port: object())
    assert getattr(urllib.request, "_opener") is None  # noqa: B009
    assert entry.main([]) == 0
    [opener] = started
    assert opener is not None
    with pytest.raises(OutboundBlocked):
        urllib.request.urlopen("https://evil.example/", timeout=1)


# --- the OAuth sign-in clients (httpx) -------------------------------------------------------

PROVIDER = Provider(
    name="google",
    client_id="test-client",
    authorize_url="https://accounts.google.com/o/oauth2/v2/auth",
    token_url="https://oauth2.googleapis.com/token",
    scope="openid email",
    jwks_url="https://www.googleapis.com/oauth2/v3/certs",
    client_secret="public-test-secret",
)
PROVIDER_HOSTS = ("oauth2.googleapis.com", "www.googleapis.com", "api.github.com")


class Redirecting:
    """Answers each provider host with a redirect to ``target``; records every host asked."""

    def __init__(self, target: str, status: int) -> None:
        self.target = target
        self.status = status
        self.hosts: list[str] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.hosts.append(request.url.host)
        if request.url.host in PROVIDER_HOSTS:
            return httpx.Response(self.status, headers={"Location": self.target})
        return httpx.Response(200, json={"keys": []})


def test_the_oauth_client_ignores_the_environment_and_follows_no_redirect() -> None:
    client = oauth_client._client(PROVIDER, "https://classbeat.example/cb", None)  # pyright: ignore[reportPrivateUsage]
    assert client.follow_redirects is False
    assert client.trust_env is False


@pytest.mark.parametrize("status", [302, 307, 308])
async def test_a_token_endpoint_redirect_is_not_followed_and_the_secret_goes_nowhere(
    status: int,
) -> None:
    upstream = Redirecting("https://evil.example/collect", status)
    runtime = oauth_client.OAuthRuntime(
        providers={"google": PROVIDER}, transport=httpx.MockTransport(upstream)
    )
    with pytest.raises(oauth_client.ProviderError):
        await oauth_client.identify(
            runtime,
            PROVIDER,
            "https://classbeat.example/cb",
            code="code-1",
            verifier="v" * 43,
            nonce="n",
        )
    assert upstream.hosts == ["oauth2.googleapis.com"]


async def test_a_jwks_redirect_is_not_followed() -> None:
    upstream = Redirecting("https://evil.example/keys", 302)
    with pytest.raises(httpx.HTTPStatusError):
        await oauth_client._fetch_jwks_with(  # pyright: ignore[reportPrivateUsage]
            httpx.MockTransport(upstream), PROVIDER.jwks_url or ""
        )
    assert upstream.hosts == ["www.googleapis.com"]


async def test_a_github_api_redirect_is_not_followed_with_the_access_token() -> None:
    """The GitHub identity calls carry the user's access token in ``Authorization``."""
    upstream = Redirecting("https://evil.example/u", 302)
    client = oauth_client._client(  # pyright: ignore[reportPrivateUsage]
        PROVIDER, "https://classbeat.example/cb", httpx.MockTransport(upstream)
    )
    client.token = {"access_token": "test-token", "token_type": "Bearer"}
    async with client:
        response = await client.get(
            "https://api.github.com/user", headers={"Authorization": "Bearer test-token"}
        )
    assert response.status_code == 302
    assert upstream.hosts == ["api.github.com"]


async def test_the_jwks_client_ignores_the_environment_and_follows_no_redirect(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    kwargs: list[dict[str, Any]] = []
    real = httpx.AsyncClient

    def spy(**given: Any) -> httpx.AsyncClient:
        kwargs.append(given)
        return real(**given)

    monkeypatch.setattr(oauth_client.httpx, "AsyncClient", spy)
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json={"keys": []}))
    await oauth_client._fetch_jwks_with(  # pyright: ignore[reportPrivateUsage]
        transport, PROVIDER.jwks_url or ""
    )
    [given] = kwargs
    assert given["trust_env"] is False
    assert given["follow_redirects"] is False


def test_the_tls_context_of_the_sign_in_clients_verifies() -> None:
    """``verify=`` is this context: a client with verification off would not be caught otherwise."""
    context = egress.tls_client_context()
    assert context.verify_mode == ssl.CERT_REQUIRED
    assert context.check_hostname is True
    assert context.keylog_filename is None
