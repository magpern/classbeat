"""An in-process fake of Swedebeat's catalogue (ADR 0016 §5, B1). Never the network.

``CatalogueFake`` is an ``httpx.AsyncBaseTransport`` for ``egress.swedebeat_cloud_test_client``,
so the client's allowlist, redirect and deadline checks run in front of it and no socket is
opened. It answers ``GET /track/extendedsearch`` the way the library's search does, and records
every request's URL and headers (the token included, so a test can see which user's token was
sent). Nothing here is a real token or a real catalogue.

Hits are chosen from the query text:

- a query that names ``Blue Monday`` returns that title, with extra fields a parser must drop
  (a catalogue id, a URL, an access-key shape);
- a query that names ``Bizarre`` returns a different title by the same artist (an unsure match);
- anything else returns no hit (missing).

``status``, ``retry_after``, ``redirect``, ``body`` and ``encoding`` inject one answer for every
request until changed.
"""

from dataclasses import dataclass, field
from typing import Any
from urllib.parse import unquote

import httpx

FOUND_TITLE = "Blue Monday"
UNSURE_TITLE = "Bizarre"
ARTIST = "New Order"
FOUND_SECONDS = 241
UNSURE_SECONDS = 380
PLANTED_REFERENCE = "sb-ref-not-stored"
PLANTED_URL = "https://evil.example/track?sig=planted-signature"
PLANTED_KEY = "AKIA012345678901"  # credential-shaped; shorter than an AWS access key id
PLANTED_ARTIST_ID = "artist-id-not-stored"


def _hit(title: str, artist: str, duration: int) -> dict[str, Any]:
    return {
        "reference": PLANTED_REFERENCE,
        "title": title,
        "artist": {"title": artist, "id": PLANTED_ARTIST_ID},
        "duration": duration,
        "url": PLANTED_URL,
        "accessKeyId": PLANTED_KEY,
    }


def hits_for(url: str) -> list[dict[str, Any]]:
    text = unquote(url)
    if FOUND_TITLE in text:
        return [_hit(FOUND_TITLE, ARTIST, FOUND_SECONDS)]
    if UNSURE_TITLE in text:
        return [_hit("Bizarre Love Triangle", ARTIST, UNSURE_SECONDS)]
    return []


@dataclass
class Call:
    method: str
    url: str
    headers: dict[str, str]

    @property
    def token(self) -> str | None:
        return self.headers.get("x-auth-token")


@dataclass
class CatalogueFake(httpx.AsyncBaseTransport):
    calls: list[Call] = field(default_factory=list)
    status: int = 200
    retry_after: str | None = None
    redirect: str | None = None
    body: bytes | None = None
    encoding: str | None = None

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(
            Call(
                request.method,
                str(request.url),
                {name.lower(): value for name, value in request.headers.items()},
            )
        )
        if self.redirect is not None:
            return httpx.Response(302, headers={"location": self.redirect})
        extra: dict[str, str] = {}
        if self.retry_after is not None:
            extra["retry-after"] = self.retry_after
        if self.encoding is not None:
            extra["content-encoding"] = self.encoding
        if self.body is not None:
            return httpx.Response(self.status, headers=extra, content=self.body)
        if self.status != 200:
            return httpx.Response(self.status, headers=extra, json={"message": "no"})
        return httpx.Response(
            self.status,
            json={
                "data": {"hit": hits_for(str(request.url))},
                "url": PLANTED_URL,
                "reference": PLANTED_REFERENCE,
            },
        )
