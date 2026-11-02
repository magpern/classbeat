"""One catalogue search over the dedicated client (ADR 0016 sections 4 and 5, B1).

``search`` sends one ``GET`` of ``catalogue.search_url`` with exactly three headers: ``Accept``,
the app's ``User-Agent`` and the user's token in ``X-Auth-Token`` (the client adds none of its
own). The answer becomes the kept hits (``catalogue.parse_hits``) or a ``SearchFailure`` with a
category, decided from the status only, never from the body:

- ``401`` or ``403``: ``needs_reconnect`` (the caller flips the connection, ADR 0015);
- ``429``: ``rate_limited`` with Swedebeat's ``Retry-After`` (whole seconds, at most a day);
- ``5xx``, a timeout, the total deadline or a failed connection: ``upstream_unavailable``;
- a redirect: ``OutboundBlocked`` (the token never follows one; a hop to another host is refused
  by the client itself before it is sent);
- any other status, a body over ``catalogue.MAX_ANSWER_BYTES``, one that is not JSON, or a
  ``Content-Encoding`` other than identity (the body is read raw, never decompressed):
  ``invalid_response``.

Nothing here logs, and no failure carries the URL (it holds the song's title), a header or the
body: ``SearchFailure`` and the re-raised ``OutboundBlocked`` are raised after their ``except``
block, so they chain nothing.
"""

from typing import Final

import httpx

from classbeat.connections.errors import retry_after_of
from classbeat.egress import SWEDEBEAT_CLOUD_HOST, OutboundBlocked, blocked
from classbeat.swedebeat_match import catalogue
from classbeat.swedebeat_match.catalogue import Hit

_OK: Final = 200
_UNAUTHORIZED: Final = frozenset({401, 403})
_RATE_LIMITED: Final = 429


class SearchFailure(Exception):
    """A search that gave no hits to judge. ``args == (category,)``."""

    def __init__(self, category: str, retry_after: int | None = None) -> None:
        super().__init__(category)
        self.category = category
        self.retry_after = retry_after if category == "rate_limited" else None


def headers(token: str) -> dict[str, str]:
    """The only headers a search carries."""
    return {
        "Accept": "application/json",
        "User-Agent": catalogue.USER_AGENT,
        "X-Auth-Token": token,
    }


def _declared_too_big(response: httpx.Response) -> bool:
    declared = response.headers.get("content-length")
    if declared is None:
        return False
    text = declared.strip()
    return text.isdigit() and int(text) > catalogue.MAX_ANSWER_BYTES


async def _body(response: httpx.Response) -> bytes | None:
    """The answer's raw body, or None when it must not be parsed.

    A ``Content-Encoding`` other than identity is refused before a single byte is decoded (a
    small gzip of a huge document must not expand). A declared length over the cap is refused
    the same way. The streamed count is checked too, and the rest of an oversized body is not
    read."""
    encoding = response.headers.get("content-encoding")
    if encoding is not None and encoding.strip().lower() not in ("", "identity"):
        return None
    if _declared_too_big(response):
        return None
    chunks: list[bytes] = []
    size = 0
    async for chunk in response.aiter_raw():
        size += len(chunk)
        if size > catalogue.MAX_ANSWER_BYTES:
            return None
        chunks.append(chunk)
    return b"".join(chunks)


async def search(client: httpx.AsyncClient, token: str, title: str, artist: str) -> list[Hit]:
    """The kept hits for one song (see the module docstring)."""
    url = catalogue.search_url(title, artist)
    status: int | None = None
    body: bytes | None = None
    retry_after: int | None = None
    category: str | None = None
    refusal: OutboundBlocked | None = None
    try:
        async with client.stream("GET", url, headers=headers(token)) as response:
            status = response.status_code
            if status == _OK:
                body = await _body(response)
            elif status == _RATE_LIMITED:
                retry_after = retry_after_of(response)
    except OutboundBlocked as refused:
        refusal = OutboundBlocked(refused.host, after_request=refused.after_request)
    except (httpx.HTTPError, TimeoutError, OSError):
        category = "upstream_unavailable"
    if refusal is not None:
        raise refusal
    if category is None:
        category = _category(status, body)
    if category is not None:
        raise SearchFailure(category, retry_after)
    hits = catalogue.parse_hits(body or b"")
    if hits is None:
        raise SearchFailure("invalid_response")
    return hits


def _category(status: int | None, body: bytes | None) -> str | None:
    if status is None:
        return "upstream_unavailable"
    if status == _OK:
        return None if body is not None else "invalid_response"
    if status in _UNAUTHORIZED:
        return "needs_reconnect"
    if status == _RATE_LIMITED:
        return "rate_limited"
    if 500 <= status <= 599:
        return "upstream_unavailable"
    if 300 <= status <= 399:
        # A same-host redirect comes back as an answer: refused, never followed.
        raise blocked(SWEDEBEAT_CLOUD_HOST, after_request=True)
    return "invalid_response"
