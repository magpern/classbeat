"""An in-process fake of the MOWL temporary-link media host (ADR 0016 §4, §8). Never the network.

``FakeMedia`` is an ``httpx.AsyncBaseTransport``: it is handed to ``egress.mowl_media_async_client``
as ``inner`` (the pattern of ``SpotifyHTTPS``), so the client's allowlist, redirect and deadline
checks run in front of it and no socket is opened. Nothing about the real host is known (it is not
guessed), so the fake answers as the invented name ``media.fake.test``; a test builds the client
with
``Allowlist(frozenset({FakeMedia.HOST}))`` and passes it to the code under test.

A link is minted by ``FakeMedia.link`` (the fake MOWL does it for ``TemporaryLinks``): a path and a
random signature in the query, a bearer capability like the real one; ``signatures`` holds every one
so a test can search logs and responses for them. Each link has a **behaviour** (failure injection):

- ``ok``: ``200`` with a short generated WAV (a sine, ``seconds`` long), ``audio/wav``;
- ``octet``: the same audio as ``application/octet-stream`` (CDNs often send that);
- ``undecodable``: ``200``, ``audio/mpeg``, random bytes no decoder opens;
- ``status``: the HTTP status ``code`` (4xx or 5xx), a small body;
- ``redirect``: ``302`` to another path of the same host; ``redirect_away``: ``302`` to
  ``other`` (another host, unlisted);
- ``oversize``: ``200`` declaring ``Content-Length`` ``size`` and streaming that many zero bytes
  lazily (never held in memory); ``oversize_stream``: the same with no ``Content-Length``;
- ``slow``: ``200`` whose body trickles ``chunk`` bytes every ``delay`` seconds, ``chunks`` times.

``requests`` records ``(method, host, path)`` (never the query, so never a signature) and
``headers`` the header names and values of each request, for "none of our headers" tests.
"""

import asyncio
import io
import math
import secrets
import struct
import wave
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

import httpx

OTHER_HOST = "other.fake.test"  # a name no list holds
BEHAVIOURS = (
    "ok",
    "octet",
    "undecodable",
    "status",
    "redirect",
    "redirect_away",
    "oversize",
    "oversize_stream",
    "slow",
)


def wav_bytes(seconds: float = 2.0, rate: int = 8000) -> bytes:
    """A short mono 16-bit WAV of a 440 Hz sine, generated, never a recording."""
    frames = b"".join(
        struct.pack("<h", int(8000 * math.sin(2 * math.pi * 440 * i / rate)))
        for i in range(int(seconds * rate))
    )
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(rate)
        out.writeframes(frames)
    return buffer.getvalue()


@dataclass
class Link:
    name: str
    behaviour: str = "ok"
    options: dict[str, Any] = field(default_factory=lambda: dict[str, Any]())


class _Chunks(httpx.AsyncByteStream):
    def __init__(self, chunks: AsyncIterator[bytes]) -> None:
        self._chunks = chunks

    async def __aiter__(self) -> AsyncIterator[bytes]:
        async for chunk in self._chunks:
            yield chunk


async def _body(data: bytes) -> AsyncIterator[bytes]:
    yield data


async def _zeros(size: int) -> AsyncIterator[bytes]:
    block = b"\0" * (1 << 20)
    left = size
    while left > 0:
        yield block[: min(left, len(block))]
        left -= len(block)


async def _trickle(chunk: int, delay: float, chunks: int) -> AsyncIterator[bytes]:
    for _ in range(chunks):
        await asyncio.sleep(delay)
        yield b"\1" * chunk


class FakeMedia(httpx.AsyncBaseTransport):
    HOST = "media.fake.test"

    def __init__(self) -> None:
        self.links: dict[str, Link] = {}
        self.signatures: list[str] = []
        self.requests: list[tuple[str, str, str]] = []
        self.headers: list[dict[str, str]] = []

    def link(self, behaviour: str = "ok", **options: Any) -> str:
        """Mint a link (https, the fake host, a random path and signature) with ``behaviour``."""
        if behaviour not in BEHAVIOURS:
            raise ValueError(behaviour)
        name = secrets.token_hex(8)
        signature = secrets.token_hex(16)
        self.links[name] = Link(name, behaviour, options)
        self.signatures.append(signature)
        return f"https://{self.HOST}/media/{name}/mix.mp3?sig={signature}"

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        host = request.url.host
        self.requests.append((request.method, host, request.url.path))
        self.headers.append({k.lower(): v for k, v in request.headers.items()})
        parts = request.url.path.split("/")
        link = self.links.get(parts[2]) if len(parts) > 3 and parts[1] == "media" else None
        if link is None or host != self.HOST or request.method != "GET":
            return httpx.Response(404, content=b"not found", request=request)
        return self._answer(link, parts[3], request)

    def _answer(self, link: Link, leaf: str, request: httpx.Request) -> httpx.Response:
        o, kind = link.options, link.behaviour
        if kind == "redirect" and leaf == "mix.mp3":
            return httpx.Response(
                302, headers={"location": f"/media/{link.name}/moved.mp3"}, request=request
            )
        if kind == "redirect_away":
            target = f"https://{o.get('other', OTHER_HOST)}/media/{link.name}/mix.mp3"
            return httpx.Response(302, headers={"location": target}, request=request)
        if kind == "status":
            return httpx.Response(int(o.get("code", 403)), content=b"refused", request=request)
        if kind == "undecodable":
            data = secrets.token_bytes(int(o.get("size", 4096)))
            return self._whole(data, "audio/mpeg", request)
        if kind == "oversize":
            size = int(o.get("size", 250 * 1024 * 1024 + 1))
            headers = {"content-type": "audio/mpeg", "content-length": str(size)}
            return httpx.Response(
                200, headers=headers, stream=_Chunks(_zeros(size)), request=request
            )
        if kind == "oversize_stream":
            size = int(o.get("size", 250 * 1024 * 1024 + 1))
            headers = {"content-type": "audio/mpeg"}
            return httpx.Response(
                200, headers=headers, stream=_Chunks(_zeros(size)), request=request
            )
        if kind == "slow":
            chunks = _trickle(
                int(o.get("chunk", 1)), float(o.get("delay", 0.05)), int(o.get("chunks", 1000))
            )
            headers = {"content-type": "audio/mpeg"}
            return httpx.Response(200, headers=headers, stream=_Chunks(chunks), request=request)
        # ok, octet, and the moved-to path of a redirect
        data = wav_bytes(float(o.get("seconds", 2.0)))
        kind_type = "application/octet-stream" if kind == "octet" else "audio/wav"
        return self._whole(data, kind_type, request)

    @staticmethod
    def _whole(data: bytes, content_type: str, request: httpx.Request) -> httpx.Response:
        headers = {"content-type": content_type, "content-length": str(len(data))}
        return httpx.Response(200, headers=headers, stream=_Chunks(_body(data)), request=request)
