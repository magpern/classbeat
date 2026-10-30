"""The Spotify hosts on both allowlists (ADR 0009 §10, threat model T73): the worker's urllib
opener (the library's calls) and the Spotify client of ``classbeat.spotify.http`` (web and
worker). A host off the list, a redirect off it, a ``next`` URL to an unlisted host and ``http``
are refused on both, with the fake Spotify as the network; never the network."""

import asyncio
import io
import json
import ssl
import time
import urllib.error
import urllib.request
import urllib.response
from email.message import Message
from pathlib import Path
from typing import Any

import httpx
import pytest

from classbeat import egress
from classbeat.egress import (
    SPOTIFY_ALLOWLIST,
    WORKER_ALLOWLIST,
    OutboundBlocked,
    RequestDeadline,
    build_allowlist_opener,
    spotify_async_client,
)
from spotifycore import UNLISTED_HOST, FakeSpotify, track_id
from spotifyfake import CLIENT_A, USER_A, SpotifyHTTPS, spotify_client, worker_opener

API = "https://api.spotify.com/v1"

REFUSED = [
    ("https://evil.example/v1/me", "evil.example"),
    ("https://spotify.com/v1/me", "spotify.com"),
    ("https://api.spotify.com.evil.example/v1/me", "api.spotify.com.evil.example"),
    ("https://api.spotify.com:8443/v1/me", "api.spotify.com"),
    ("https://user:pw@api.spotify.com/v1/me", "api.spotify.com"),
    ("https://169.254.169.254/latest/meta-data", "169.254.169.254"),
    ("http://api.spotify.com/v1/me", "api.spotify.com"),
    ("http://accounts.spotify.com/api/token", "accounts.spotify.com"),
    (f"https://{UNLISTED_HOST}/v1/next?offset=50", UNLISTED_HOST),
]


def _bearer(access: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {access}"}


@pytest.fixture
def access(spotify: FakeSpotify) -> str:
    return spotify.grant(CLIENT_A, USER_A)[0]


# --- the worker's urllib opener -------------------------------------------------------------


def test_the_worker_opener_reaches_both_spotify_hosts(spotify: FakeSpotify, access: str) -> None:
    opener = worker_opener(spotify)
    request = urllib.request.Request(f"{API}/me", headers=_bearer(access))  # noqa: S310
    with opener.open(request, timeout=5) as response:
        assert json.loads(response.read())["id"] == USER_A
    form = b"grant_type=refresh_token&refresh_token=x&client_id=" + CLIENT_A.encode()
    token = urllib.request.Request(
        "https://accounts.spotify.com/api/token",
        data=form,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    with pytest.raises(urllib.error.HTTPError) as raised:  # reached it: invalid_grant
        opener.open(token, timeout=5)
    assert raised.value.code == 400
    assert [s.host for s in spotify.requests()] == ["api.spotify.com", "accounts.spotify.com"]


@pytest.mark.parametrize(("url", "host"), REFUSED)
def test_the_worker_opener_refuses_anything_else(spotify: FakeSpotify, url: str, host: str) -> None:
    with pytest.raises(OutboundBlocked) as raised:
        worker_opener(spotify).open(url, timeout=5)
    assert (raised.value.host, raised.value.after_request) == (host, False)
    assert spotify.requests() == []


class Redirecting(urllib.request.BaseHandler):
    """A network handler answering every Spotify request with a redirect to ``target``."""

    def __init__(self, target: str) -> None:
        self.target = target
        self.seen: list[str] = []

    def https_open(self, req: urllib.request.Request) -> Any:
        self.seen.append(req.full_url)
        headers = Message()
        headers["Location"] = self.target
        response = urllib.response.addinfourl(io.BytesIO(b""), headers, req.full_url, 302)
        response.msg = "Found"  # pyright: ignore[reportAttributeAccessIssue]
        return response


@pytest.mark.parametrize(
    "target",
    [
        "https://evil.example/steal",
        "http://api.spotify.com/v1/me",
        "https://accounts.spotify.com/api/token",  # the other Spotify host: no token crosses
        "https://api.mowl.com/v1/programs",
        f"https://{UNLISTED_HOST}/v1/next",
    ],
)
def test_the_worker_opener_refuses_a_redirect_off_the_host(target: str) -> None:
    network = Redirecting(target)
    opener = build_allowlist_opener(WORKER_ALLOWLIST, https_handler=network)
    request = urllib.request.Request(f"{API}/me", headers=_bearer("planted"))  # noqa: S310
    with pytest.raises(OutboundBlocked) as raised:
        opener.open(request, timeout=5)
    assert raised.value.after_request is True
    assert network.seen == [f"{API}/me"]


def test_the_authorize_redirect_to_the_callback_is_refused(spotify: FakeSpotify) -> None:
    """The authorize page's 302 goes to classbeat's own callback, which the worker never calls."""
    spotify.sign_in(USER_A)
    url = (
        "https://accounts.spotify.com/authorize?client_id=" + CLIENT_A + "&response_type=code"
        "&redirect_uri=http%3A%2F%2F127.0.0.1%3A8000%2Foauth%2Fspotify%2Fcallback"
        "&code_challenge_method=S256&code_challenge=abc&state=s"
    )
    with pytest.raises(OutboundBlocked) as raised:
        worker_opener(spotify).open(url, timeout=5)
    assert (raised.value.host, raised.value.after_request) == ("127.0.0.1", True)
    assert len(spotify.requests()) == 1


def _paged(spotify: FakeSpotify) -> str:
    uris = [f"spotify:track:{track_id(i)}" for i in range(60)]
    playlist = spotify.add_playlist(USER_A, "Sixty", uris).id
    spotify.inject("next_unlisted", path=f"/v1/playlists/{playlist}/items")
    return playlist


def test_the_worker_opener_refuses_a_next_url_to_an_unlisted_host(
    spotify: FakeSpotify, access: str
) -> None:
    playlist = _paged(spotify)
    opener = worker_opener(spotify)
    request = urllib.request.Request(  # noqa: S310
        f"{API}/playlists/{playlist}/items?limit=50", headers=_bearer(access)
    )
    with opener.open(request, timeout=5) as response:
        page = json.loads(response.read())
    assert page["next"].startswith(f"https://{UNLISTED_HOST}/")
    following = urllib.request.Request(page["next"], headers=_bearer(access))  # noqa: S310
    with pytest.raises(OutboundBlocked) as raised:
        opener.open(following, timeout=5)
    assert raised.value.host == UNLISTED_HOST
    assert [s.host for s in spotify.requests()] == ["api.spotify.com"]


def test_the_library_reaches_spotify_through_the_installed_worker_opener(
    spotify: FakeSpotify, access: str
) -> None:
    """``urlopen``, as the library calls it, goes through the installed worker opener."""
    urllib.request.install_opener(worker_opener(spotify))
    request = urllib.request.Request(f"{API}/me", headers=_bearer(access))  # noqa: S310
    with urllib.request.urlopen(request, timeout=5) as response:  # noqa: S310
        assert json.loads(response.read())["id"] == USER_A
    with pytest.raises(OutboundBlocked):
        urllib.request.urlopen(f"https://{UNLISTED_HOST}/x", timeout=5)


# --- the Spotify client (classbeat.spotify.http) ---------------------------------------------


async def test_the_spotify_client_reaches_both_spotify_hosts(
    spotify: FakeSpotify, access: str
) -> None:
    async with spotify_client(spotify) as client:
        me = await client.get(f"{API}/me", headers=_bearer(access))
        token = await client.post(
            "https://accounts.spotify.com/api/token",
            data={"grant_type": "refresh_token", "refresh_token": "x", "client_id": CLIENT_A},
        )
    assert me.json()["id"] == USER_A
    assert token.json()["error"] == "invalid_grant"


@pytest.mark.parametrize(
    ("url", "host"),
    [
        *REFUSED,
        ("https://api.mowl.com/v1/programs", "api.mowl.com"),
        ("https://api.anthropic.com/v1/messages", "api.anthropic.com"),
    ],
)
async def test_the_spotify_client_refuses_anything_else(
    spotify: FakeSpotify, url: str, host: str
) -> None:
    """Its own list: not even the worker's other hosts, in the web process or the worker."""
    async with spotify_client(spotify) as client:
        with pytest.raises((OutboundBlocked, httpx.InvalidURL)) as raised:
            await client.get(url)
    if isinstance(raised.value, OutboundBlocked):
        assert raised.value.host == host
    assert spotify.requests() == []


class Redirect(httpx.AsyncBaseTransport):
    def __init__(self, target: str) -> None:
        self.target = target
        self.seen: list[str] = []

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self.seen.append(str(request.url))
        return httpx.Response(307, headers={"Location": self.target})


@pytest.mark.parametrize(
    "target",
    [
        "https://evil.example/steal",
        "https://accounts.spotify.com/api/token",
        "https://api.mowl.com/v1/programs",
        f"https://{UNLISTED_HOST}/v1/next",
    ],
)
@pytest.mark.parametrize("follow", [False, True])
async def test_the_spotify_client_refuses_a_redirect_off_the_host(
    target: str, follow: bool
) -> None:
    network = Redirect(target)
    async with spotify_async_client(inner=network) as client:
        with pytest.raises(OutboundBlocked) as raised:
            await client.get(f"{API}/me", headers=_bearer("planted"), follow_redirects=follow)
    assert raised.value.after_request is True
    assert network.seen == [f"{API}/me"]


async def test_the_spotify_client_does_not_follow_a_same_host_redirect() -> None:
    network = Redirect("http://api.spotify.com/v1/other")
    async with spotify_async_client(inner=network) as client:
        assert client.follow_redirects is False
        response = await client.get(f"{API}/me")
        assert response.status_code == 307  # handed back, not followed
        with pytest.raises(OutboundBlocked):  # and an http hop is refused if followed
            await client.get(f"{API}/me", follow_redirects=True)
    assert network.seen == [f"{API}/me", f"{API}/me"]


async def test_the_spotify_client_refuses_a_next_url_to_an_unlisted_host(
    spotify: FakeSpotify, access: str
) -> None:
    playlist = _paged(spotify)
    async with spotify_client(spotify) as client:
        page = (
            await client.get(f"{API}/playlists/{playlist}/items", headers=_bearer(access))
        ).json()
        with pytest.raises(OutboundBlocked) as raised:
            await client.get(page["next"], headers=_bearer(access))
    assert raised.value.host == UNLISTED_HOST
    assert [s.host for s in spotify.requests()] == ["api.spotify.com"]


# --- the Spotify client's total deadline -----------------------------------------------------


class Slow(httpx.AsyncBaseTransport):
    def __init__(self, seconds: float) -> None:
        self.seconds = seconds

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(self.seconds)
        return httpx.Response(200, json={"id": "late"})


async def test_a_slow_answer_ends_at_the_total_deadline() -> None:
    started = time.monotonic()
    async with spotify_async_client(total_seconds=0.2, inner=Slow(5)) as client:
        with pytest.raises(RequestDeadline):
            await client.get(f"{API}/me")
    assert time.monotonic() - started < 2


async def test_a_trickling_body_ends_at_the_total_deadline(
    spotify: FakeSpotify, access: str
) -> None:
    """Each byte arrives well within httpx's per-read timeout; only the total ends it."""
    spotify.inject("trickle", path="/v1/me", delay=0.03)
    started = time.monotonic()
    async with spotify_client(spotify, total_seconds=0.25) as client:
        with pytest.raises(RequestDeadline):
            await client.get(f"{API}/me", headers=_bearer(access))
    assert time.monotonic() - started < 2


async def test_a_streamed_body_also_ends_at_the_total_deadline(
    spotify: FakeSpotify, access: str
) -> None:
    spotify.inject("trickle", path="/v1/me", delay=0.03)
    async with (
        spotify_client(spotify, total_seconds=0.25) as client,
        client.stream("GET", f"{API}/me", headers=_bearer(access)) as response,
    ):
        with pytest.raises(RequestDeadline):
            async for _ in response.aiter_bytes():
                pass


async def test_a_prompt_answer_is_untouched(spotify: FakeSpotify, access: str) -> None:
    async with spotify_client(spotify, total_seconds=0.5) as client:
        assert (await client.get(f"{API}/me", headers=_bearer(access))).json()["id"] == USER_A


@pytest.mark.parametrize("seconds", [0, -1.0])
def test_the_total_deadline_is_positive(seconds: float) -> None:
    with pytest.raises(ValueError, match="positive"):
        spotify_async_client(total_seconds=seconds)


async def test_the_default_total_deadline_is_the_refresh_timeout() -> None:
    async with spotify_async_client() as client:
        assert client.timeout == httpx.Timeout(egress.SPOTIFY_TOTAL_SECONDS)
        assert egress.SPOTIFY_TOTAL_SECONDS == 10.0
        transport = client._transport  # pyright: ignore[reportPrivateUsage]
        assert isinstance(transport, egress.AllowlistTransport)
        assert transport._total_seconds == 10.0  # pyright: ignore[reportPrivateUsage]


# --- the Spotify client's configuration -----------------------------------------------------

PROXY_VARIABLES = ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy", "ALL_PROXY")


@pytest.mark.parametrize("variable", PROXY_VARIABLES)
async def test_the_spotify_client_ignores_proxy_settings(
    variable: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(variable, "http://proxy.evil.example:3128")
    async with spotify_async_client() as client:
        assert client.trust_env is False
        assert client._mounts == {}  # pyright: ignore[reportPrivateUsage]
        transport = client._transport  # pyright: ignore[reportPrivateUsage]
        assert isinstance(transport, egress.AllowlistTransport)
        assert transport._allowlist is SPOTIFY_ALLOWLIST  # pyright: ignore[reportPrivateUsage]


def test_the_spotify_list_is_exactly_the_two_hosts() -> None:
    assert SPOTIFY_ALLOWLIST.hosts == frozenset({"api.spotify.com", "accounts.spotify.com"})
    assert SPOTIFY_ALLOWLIST.hosts <= WORKER_ALLOWLIST.hosts


def test_the_spotify_client_takes_no_allowlist() -> None:
    with pytest.raises(TypeError):
        spotify_async_client(WORKER_ALLOWLIST)  # pyright: ignore[reportCallIssue]
    with pytest.raises(TypeError):
        spotify_async_client(allowlist=WORKER_ALLOWLIST)  # pyright: ignore[reportCallIssue]


def test_the_spotify_client_writes_no_tls_key_log(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    key_log = tmp_path / "keys.log"
    monkeypatch.setenv("SSLKEYLOGFILE", str(key_log))
    asked: list[str | None] = []
    real = egress.tls_client_context

    def spy(*, cafile: str | None = None) -> ssl.SSLContext:
        asked.append(cafile)
        return real(cafile=cafile)

    monkeypatch.setattr(egress, "tls_client_context", spy)
    client = spotify_async_client()  # built only, never used: nothing to close
    transport = client._transport  # pyright: ignore[reportPrivateUsage]
    assert isinstance(transport, egress.AllowlistTransport)
    network = transport._inner  # pyright: ignore[reportPrivateUsage]
    assert isinstance(network, httpx.AsyncHTTPTransport)
    context = network._pool._ssl_context  # pyright: ignore[reportPrivateUsage]
    assert context is not None
    assert asked == [None]  # the urllib path's trust store, through our context
    assert context.keylog_filename is None
    assert (context.verify_mode, context.check_hostname) == (ssl.CERT_REQUIRED, True)
    assert not key_log.exists()


def test_the_urllib_fake_is_only_a_network_handler(spotify: FakeSpotify) -> None:
    """The fake replaces the network only: the allowlist guard still runs in front of it."""
    handler = SpotifyHTTPS(spotify)
    opener = build_allowlist_opener(WORKER_ALLOWLIST, https_handler=handler)
    with pytest.raises(OutboundBlocked):
        opener.open("https://evil.example/", timeout=5)
    assert spotify.requests() == []
