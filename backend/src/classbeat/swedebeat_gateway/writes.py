"""The gateway's playlist writes and the one read that settles them (ADR 0036 sections 6 and 8,
SR C7): ``create_playlist``, ``publish_playlist``, ``merge_publish`` and ``list_shared``, nothing
else (never ``PATCH``). Each takes a client of ``classbeat.egress_gateway`` (one per call), the
client key and the user's bearer, and sends ``X-API-Key``, ``Authorization: Bearer`` and
``Accept`` with only what the call needs (``write_policy``).

- ``create_playlist``: one ``POST /playlists`` with the studio's JSON body; the new
  ``playlist_id`` (``201``).
- ``publish_playlist``: one ``POST /playlists/{playlist_id}/publish`` with
  ``{"confirm_irreversible": true}``; the answer must echo the id as public.
- ``merge_publish``: one ``POST /playlists/merge-publish?name=...&confirm_irreversible=true``
  whose body is the rendered MP3 **streamed from its file** (``audio/mpeg``, never read whole
  into memory) and whose ``X-Tracks`` is the studio's UTF-8 JSON; its client is opened with
  ``egress_gateway.MERGE_SECONDS`` (900 s, the upload included). The published mix's ``url``.
- ``list_shared``: ``GET /playlists/shared?page=n`` for every page from 1 until a page comes back
  empty, at most 50; the ids of the caller's own published playlists. A read: it never writes.

**Outcomes.** A write that the gateway refused, or that provably never left (a connection that
was never made, a refusal of this exact request before it was sent), raises
``WriteFailure(category, sent=False)``: nothing was written. Anything that may have reached the
gateway without a clear answer (a timeout or a connection lost after sending, a ``5xx``, a
redirect, an unreadable success) raises ``WriteFailure("uncertain", sent=True)``. A failed read
raises ``WriteFailure`` with ``read=True``. No message, key or bearer is ever in an exception:
each is raised after its ``except`` block, so it chains nothing (httpx's errors hold the request).
"""

from collections.abc import AsyncIterator
from pathlib import Path
from typing import Final
from urllib.parse import urlencode

import httpx
from pydantic import SecretStr

from classbeat.connections.errors import retry_after_of
from classbeat.egress import OutboundBlocked
from classbeat.swedebeat_gateway.answer import read_capped
from classbeat.swedebeat_gateway.login_policy import BEARER_MAX, BEARER_MIN
from classbeat.swedebeat_gateway.status_policy import is_unknown_client
from classbeat.swedebeat_gateway.track_policy import AUDIO_MPEG
from classbeat.swedebeat_gateway.write_policy import (
    ANSWER_MAX,
    CREATE_PATH,
    MERGE_PATH,
    PUBLISH_BODY,
    SHARED_PAGES,
    SHARED_PATH,
    parse_created,
    parse_merged,
    parse_published,
    parse_shared,
    publish_path,
    write_category,
    x_tracks_header,
)

_JSON: Final = "application/json"
_CHUNK: Final = 1 << 20
ERROR_ANSWER_MAX: Final = 16 * 1024


class WriteFailure(Exception):
    """``args == (category,)``. ``sent`` is False only when nothing can have been written."""

    def __init__(self, category: str, *, sent: bool, retry_after: int | None = None) -> None:
        super().__init__(category)
        self.category = category
        self.sent = sent
        self.retry_after = retry_after if category == "rate_limited" else None


def _headers(
    key: SecretStr, bearer: str, content_type: str | None, **extra: bytes
) -> list[tuple[bytes, bytes]]:
    if not (BEARER_MIN <= len(bearer) <= BEARER_MAX and all("!" <= c <= "~" for c in bearer)):
        raise ValueError("the bearer is 16 to 4096 printable ASCII characters")
    headers = {
        "Accept": _JSON,
        "Accept-Encoding": "identity",
        "X-API-Key": key.get_secret_value(),
        "Authorization": f"Bearer {bearer}",
    }
    if content_type is not None:
        headers["Content-Type"] = content_type
    raw = [(name.encode("ascii"), value.encode("ascii")) for name, value in headers.items()]
    return raw + [(name.replace("_", "-").encode("ascii"), value) for name, value in extra.items()]


async def _send(
    client: httpx.AsyncClient,
    method: str,
    path: str,
    headers: list[tuple[bytes, bytes]],
    content: bytes | AsyncIterator[bytes] | None,
    ok: int,
) -> bytes | None:
    """One request; the body of its ``ok`` answer (``None`` when it can't be read). Raises
    ``WriteFailure`` for anything else (see the module docstring)."""
    status: int | None = None
    body: bytes | None = None
    retry_after: int | None = None
    sent = True
    try:
        async with client.stream(method, path, headers=headers, content=content) as response:
            status = response.status_code
            if status == ok:
                body = await read_capped(response, ANSWER_MAX)
            elif status == 401:
                body = await read_capped(response, ERROR_ANSWER_MAX)
            elif status == 429:
                retry_after = retry_after_of(response)
    except OutboundBlocked as refused:
        sent = refused.after_request
    except (httpx.ConnectError, httpx.ConnectTimeout):
        sent = False  # no connection was made: nothing left this process
    except (httpx.HTTPError, TimeoutError, OSError):
        status = None
    if status is None:
        raise WriteFailure("uncertain" if sent else "upstream_unavailable", sent=sent)
    category = write_category(status, ok, unknown_client=status == 401 and is_unknown_client(body))
    if category == "uncertain":
        raise WriteFailure("uncertain", sent=True)
    if category is not None:
        raise WriteFailure(category, sent=False, retry_after=retry_after)
    return body


async def create_playlist(
    client: httpx.AsyncClient, key: SecretStr, bearer: str, body: bytes
) -> str:
    """The new playlist's id (``201``)."""
    headers = _headers(key, bearer, _JSON)
    answer = await _send(client, "POST", CREATE_PATH, headers, body, 201)
    playlist_id = parse_created(answer)
    if playlist_id is None:
        raise WriteFailure("uncertain", sent=True)
    return playlist_id


async def publish_playlist(
    client: httpx.AsyncClient, key: SecretStr, bearer: str, playlist_id: str
) -> None:
    """Publish ``playlist_id`` (permanent)."""
    path = publish_path(playlist_id)  # ValueError before anything is sent
    headers = _headers(key, bearer, _JSON)
    answer = await _send(client, "POST", path, headers, PUBLISH_BODY, 200)
    if not parse_published(answer, playlist_id):
        raise WriteFailure("uncertain", sent=True)


async def _chunks(path: Path) -> AsyncIterator[bytes]:
    with path.open("rb") as handle:
        while chunk := handle.read(_CHUNK):
            yield chunk


async def merge_publish(
    client: httpx.AsyncClient,
    key: SecretStr,
    bearer: str,
    name: str,
    tracks: list[dict[str, object]],
    mp3: Path,
) -> str:
    """Publish the rendered ``mp3`` as the mix ``name`` (permanent); its URL. ``ValueError``
    before anything is sent for a bad name or track list, or a file that is not there."""
    if not 1 <= len(name) <= 120 or not name.isprintable():
        raise ValueError("a mix name is 1 to 120 printable characters")
    x_tracks = x_tracks_header([dict(t) for t in tracks])
    size = mp3.stat().st_size  # noqa: ASYNC240 - one stat, before anything is sent
    query = urlencode({"name": name, "confirm_irreversible": "true"})
    headers = _headers(
        key, bearer, AUDIO_MPEG, X_Tracks=x_tracks, Content_Length=str(size).encode("ascii")
    )
    path = MERGE_PATH + "?" + query
    answer = await _send(client, "POST", path, headers, _chunks(mp3), 200)
    url = parse_merged(answer)
    if url is None:
        raise WriteFailure("uncertain", sent=True)
    return url


async def list_shared(client: httpx.AsyncClient, key: SecretStr, bearer: str) -> list[str]:
    """Every id of the caller's own published playlists: pages 1 to 50, until one is empty.
    ``WriteFailure`` (``read``-only: nothing was written) when a page fails or the 50th is full."""
    headers = _headers(key, bearer, None)
    found: list[str] = []
    for page in range(1, SHARED_PAGES + 1):
        try:
            answer = await _send(client, "GET", f"{SHARED_PATH}?page={page}", headers, None, 200)
        except WriteFailure as failed:
            category = failed.category
        else:
            ids = parse_shared(answer)
            if ids is None:
                category = "invalid_response"
            elif not ids:
                return found
            else:
                found.extend(ids)
                continue
        raise WriteFailure(category, sent=False)
    raise WriteFailure("invalid_response", sent=False)  # 50 full pages: it may be beyond them
