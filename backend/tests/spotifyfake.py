"""The in-process fake Spotify (ADR 0009 §13). TEST ONLY, never the network.

``tests/spotifycore.py`` is the fake itself; this module carries its answers over the two paths
classbeat uses, so each test runs the real allowlist code with the fake in place of the network:

- ``SpotifyHTTPS``: a urllib network handler, given to ``build_allowlist_opener(https_handler=)``
  (the worker's opener, which the library's ``urllib`` calls go through). A refusal raises
  ``URLError`` from ``ConnectionRefusedError`` as urllib does when it cannot connect; a dropped
  answer raises ``http.client.RemoteDisconnected``; an error status becomes ``HTTPError`` through
  the opener's error processor, as the library expects.
- ``SpotifyTransport``: an ``httpx`` transport, given to ``spotify_async_client(inner=)``
  (``classbeat.spotify.http``). A refusal raises ``httpx.ConnectError``, a drop
  ``httpx.RemoteProtocolError``.

Each answers only the two Spotify hosts. ``trickle`` answers arrive one byte per pause.
"""

import asyncio
import http.client
import io
import time
import urllib.error
import urllib.request
import urllib.response
from collections.abc import AsyncIterator
from email.message import Message
from typing import Any

import httpx
import pytest

from classbeat.egress import WORKER_ALLOWLIST, build_allowlist_opener, spotify_async_client
from spotifycore import ACCOUNTS_HOST, API_HOST, Answer, FakeSpotify

HOSTS = frozenset({API_HOST, ACCOUNTS_HOST})
# Public test values: two apps (32 hex characters), two users, the callback of a local stack.
CLIENT_A = "a0a0a0a0a0a0a0a0a0a0a0a0a0a0a0a0"
CLIENT_B = "b1b1b1b1b1b1b1b1b1b1b1b1b1b1b1b1"
USER_A = "rider-one"
USER_B = "rider-two"
CALLBACK = "http://127.0.0.1:8000/oauth/spotify/callback"
REASONS = {200: "OK", 201: "Created", 302: "Found", 400: "Bad Request", 401: "Unauthorized"}


def _reason(status: int) -> str:
    return REASONS.get(status, "Status")


class _Trickle(io.RawIOBase):
    """A body read one byte per ``pause`` seconds, as a slow server sends it."""

    def __init__(self, body: bytes, pause: float) -> None:
        self._body = body
        self._at = 0
        self._pause = pause

    def readable(self) -> bool:
        return True

    def readinto(self, buffer: Any) -> int:
        if self._at >= len(self._body) or len(buffer) == 0:
            return 0
        time.sleep(self._pause)
        buffer[0] = self._body[self._at]
        self._at += 1
        return 1


class SpotifyHTTPS(urllib.request.BaseHandler):
    """The urllib network handler that answers from ``fake``."""

    def __init__(self, fake: FakeSpotify) -> None:
        self.fake = fake

    def https_open(self, req: urllib.request.Request) -> Any:
        host = (req.host.rsplit(":", 1)[0] if ":" in req.host else req.host).lower()
        if host not in HOSTS:
            return None  # another handler's host (or none: urllib then refuses the scheme)
        data = req.data if isinstance(req.data, bytes) else None
        answer = self.fake.handle(req.get_method(), req.full_url, dict(req.header_items()), data)
        if answer.refuse:
            raise urllib.error.URLError(ConnectionRefusedError(111, "Connection refused"))
        if answer.drop:
            raise http.client.RemoteDisconnected("Remote end closed connection without response")
        headers = Message()
        for name, value in answer.headers.items():
            headers[name] = value
        body: Any = (
            io.BufferedReader(_Trickle(answer.body, answer.trickle), buffer_size=1)
            if answer.trickle
            else io.BytesIO(answer.body)
        )
        response = urllib.response.addinfourl(body, headers, req.full_url, answer.status)
        response.msg = _reason(answer.status)  # pyright: ignore[reportAttributeAccessIssue]
        return response


class _AsyncTrickle(httpx.AsyncByteStream):
    def __init__(self, body: bytes, pause: float) -> None:
        self._body = body
        self._pause = pause

    async def __aiter__(self) -> AsyncIterator[bytes]:
        for i in range(len(self._body)):
            await asyncio.sleep(self._pause)
            yield self._body[i : i + 1]


class SpotifyTransport(httpx.AsyncBaseTransport):
    """The httpx transport that answers from ``fake``."""

    def __init__(self, fake: FakeSpotify) -> None:
        self.fake = fake

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        host = request.url.host.lower()
        if host not in HOSTS:
            raise httpx.ConnectError("the fake answers the Spotify hosts only", request=request)
        body = await request.aread()
        answer: Answer = self.fake.handle(
            request.method, str(request.url), dict(request.headers.items()), body
        )
        if answer.refuse:
            raise httpx.ConnectError("Connection refused", request=request)
        if answer.drop:
            raise httpx.RemoteProtocolError(
                "Server disconnected without sending a response.", request=request
            )
        stream: httpx.AsyncByteStream = (
            _AsyncTrickle(answer.body, answer.trickle)
            if answer.trickle
            else httpx.ByteStream(answer.body)
        )
        return httpx.Response(answer.status, headers=answer.headers, stream=stream, request=request)


def worker_opener(fake: FakeSpotify) -> urllib.request.OpenerDirector:
    """The worker's urllib opener (its real allowlist) with the fake as its network."""
    return build_allowlist_opener(WORKER_ALLOWLIST, https_handler=SpotifyHTTPS(fake))


def spotify_client(fake: FakeSpotify, *, total_seconds: float = 10.0) -> httpx.AsyncClient:
    """``classbeat.spotify.http``'s client (its real allowlist) with the fake as its network."""
    return spotify_async_client(total_seconds=total_seconds, inner=SpotifyTransport(fake))


def standard(fake: FakeSpotify | None = None) -> FakeSpotify:
    """Two apps with one allowed user each, both callbacks registered, user A signed in."""
    fake = fake if fake is not None else FakeSpotify()
    fake.add_user(USER_A, "Rider One")
    fake.add_user(USER_B, "Rider Two")
    fake.add_app(CLIENT_A, redirect_uris=[CALLBACK], allowed=[USER_A])
    fake.add_app(CLIENT_B, redirect_uris=[CALLBACK], allowed=[USER_B])
    fake.sign_in(USER_A)
    return fake


@pytest.fixture
def spotify() -> FakeSpotify:
    return standard()
