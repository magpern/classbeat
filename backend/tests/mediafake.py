"""An in-process fake of the MOWL temporary-link media host (ADR 0016 §4, §8). Never the network.

``FakeMedia`` is an ``httpx.AsyncBaseTransport``: it is the transport of
``egress.mowl_media_test_client``, so the client's allowlist, redirect and deadline
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
- ``beats``: ``200`` with a generated four-on-the-floor kick track (``bpm``, default 124; 11025 Hz,
  ``seconds`` long): the library's analysis finds one song in it; ``audio/wav`` (or the option
  ``content_type``, for example ``application/octet-stream``);
- ``gzip``: the same audio, gzip-compressed, with ``Content-Encoding: gzip`` (``deflate`` and
  ``br`` are ``encoding`` options; ``bomb`` makes the body a small gzip of ``bomb`` zero bytes);
  the media client must read raw bytes and refuse it;
- ``slow``: ``200`` whose body trickles ``chunk`` bytes every ``delay`` seconds, ``chunks`` times.

``requests`` records ``(method, host, path)`` (never the query, so never a signature) and
``headers`` the header names and values of each request, for "none of our headers" tests.
"""

import asyncio
import gzip
import io
import secrets
import wave
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

import httpx
import numpy as np

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
    "beats",
    "gzip",
)


def _wav(samples: "np.ndarray[Any, Any]", rate: int) -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(rate)
        out.writeframes(samples.astype("<i2").tobytes())
    return buffer.getvalue()


def wav_bytes(seconds: float = 2.0, rate: int = 8000) -> bytes:
    """A mono 16-bit WAV of a 440 Hz sine, generated (vectorised: a long file is quick), never a
    recording."""
    n = np.arange(int(seconds * rate))
    return _wav(8000 * np.sin(2 * np.pi * 440 * n / rate), rate)


def kick_wav_bytes(seconds: float = 60.0, bpm: float = 124.0, rate: int = 11025) -> bytes:
    """A four-on-the-floor kick track (a decaying low sine on every beat): one steady song for
    the library's analysis. Generated, never a recording."""
    total = int(seconds * rate)
    y = np.zeros(total)
    t = np.arange(int(0.12 * rate)) / rate
    thump = np.sin(2 * np.pi * (55 + 60 * np.exp(-t * 40)) * t) * np.exp(-t * 30)
    step = 60.0 * rate / bpm
    for k in range(int(total / step) + 1):
        i = int(k * step)
        m = min(len(thump), total - i)
        if m > 0:
            y[i : i + m] += thump[:m]
    return _wav(20000 * y, rate)


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
        if kind == "gzip":
            return self._compressed(o, request)
        # ok, octet, beats, and the moved-to path of a redirect
        seconds = float(o.get("seconds", 2.0))
        if kind == "beats":
            data = kick_wav_bytes(seconds, float(o.get("bpm", 124.0)))
        else:
            data = wav_bytes(seconds, int(o.get("rate", 8000)))
        kind_type = "application/octet-stream" if kind == "octet" else "audio/wav"
        kind_type = str(o.get("content_type", kind_type))
        return self._whole(data, kind_type, request)

    @staticmethod
    def _compressed(o: dict[str, Any], request: httpx.Request) -> httpx.Response:
        if "bomb" in o:
            body = gzip.compress(b"\0" * int(o["bomb"]), 9)
        else:
            body = gzip.compress(wav_bytes(float(o.get("seconds", 2.0))))
        headers = {
            "content-type": "audio/wav",
            "content-encoding": str(o.get("encoding", "gzip")),
            "content-length": str(len(body)),
        }
        return httpx.Response(200, headers=headers, stream=_Chunks(_body(body)), request=request)

    @staticmethod
    def _whole(data: bytes, content_type: str, request: httpx.Request) -> httpx.Response:
        headers = {"content-type": content_type, "content-length": str(len(data))}
        return httpx.Response(200, headers=headers, stream=_Chunks(_body(data)), request=request)
