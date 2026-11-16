"""The rules of the gateway's playlist writes and their one read (ADR 0036 sections 6 and 8, SR C7).
Pure: no I/O.

- **The paths** (``docs/api.md`` of the gateway): ``POST /playlists`` (create), ``POST
  /playlists/{playlist_id}/publish`` (the template; ``publish_path`` fills it with a checked id),
  ``POST /playlists/merge-publish`` and ``GET /playlists/shared`` (reconciliation only, its
  ``page`` 1 to 50). Never ``PATCH``: a published playlist is never edited in place.
- **The status** of a write to an outcome (``write_category``): ``None`` for its success status;
  a definite refusal, nothing written: ``401 unknown client`` ``app_misconfigured``, another
  ``401`` ``bearer_refused``, **``403`` ``writes_off``** (the gateway's write guard: a normal state
  the user sees), ``429`` ``rate_limited``, ``400``/``422`` ``invalid_input``, ``404``
  ``track_unavailable`` and ``413`` ``track_too_large``; a ``5xx`` is ``uncertain`` (it may have
  been written) and a ``3xx`` ``redirect`` (refused after the request: uncertain too). Any other
  status is ``uncertain``: a write is never taken as refused unless the gateway said so.
- **The answers**: a create's ``playlist_id`` (``PLAYLIST_ID``, the gateway's own check), a
  publish's echo of that id with ``public: true``, a shared page's entries (the ids only) and a
  merge's ``url``, an ``https`` URL without userinfo, query or fragment, at most 2048 characters.
  Anything else is ``None``: the caller treats the write as uncertain.
- **The merge's track list** goes into ``X-Tracks`` as UTF-8 JSON of at most ``X_TRACKS_MAX``
  bytes (``x_tracks_header``); it is built by the studio (``studio.output_body_policy``).
"""

import json
import re
from typing import Any, Final, Literal, TypeGuard, cast
from urllib.parse import urlsplit

CREATE_PATH: Final = "/playlists"
PUBLISH_PATH: Final = "/playlists/{playlist_id}/publish"
MERGE_PATH: Final = "/playlists/merge-publish"
SHARED_PATH: Final = "/playlists/shared"
SHARED_PAGES: Final = 50  # the gateway's ``page`` is 1 to 50
PLAYLIST_ID: Final = re.compile(r"[A-Za-z0-9-]{1,64}")  # the gateway's ``_PID``
ANSWER_MAX: Final = 1024 * 1024  # a create echoes its tracks; a page lists playlists
X_TRACKS_MAX: Final = 16 * 1024
MAX_URL: Final = 2048
PUBLISH_BODY: Final = b'{"confirm_irreversible":true}'  # the JSON literal true, nothing else

WriteCategory = Literal[
    "app_misconfigured",
    "bearer_refused",
    "writes_off",
    "rate_limited",
    "invalid_input",
    "track_unavailable",
    "track_too_large",
    "uncertain",
]
_REFUSED: Final = {
    400: "invalid_input",
    403: "writes_off",
    404: "track_unavailable",
    413: "track_too_large",
    422: "invalid_input",
    429: "rate_limited",
}


def is_playlist_id(value: object) -> bool:
    return isinstance(value, str) and PLAYLIST_ID.fullmatch(value) is not None


def publish_path(playlist_id: str) -> str:
    """The publish path of a checked id; ``ValueError`` for anything that is not one."""
    if not is_playlist_id(playlist_id):
        raise ValueError("a playlist id is 1 to 64 letters, digits or '-'")
    return PUBLISH_PATH.replace("{playlist_id}", playlist_id)


def write_category(status: int, ok: int, *, unknown_client: bool) -> WriteCategory | None:
    """``None`` for ``ok``, else the outcome (see the module docstring)."""
    if status == ok:
        return None
    if status == 401:
        return "app_misconfigured" if unknown_client else "bearer_refused"
    refused = _REFUSED.get(status)
    return cast(WriteCategory, refused) if refused is not None else "uncertain"


def _object(body: bytes | None) -> dict[str, Any] | None:
    if body is None:
        return None
    try:
        answer: object = json.loads(body)
    except (ValueError, RecursionError):
        return None
    return cast(dict[str, Any], answer) if isinstance(answer, dict) else None


def parse_created(body: bytes | None) -> str | None:
    """The new playlist's id, or ``None``."""
    answer = _object(body)
    found = None if answer is None else answer.get("playlist_id")
    return found if is_playlist_id(found) else None


def parse_published(body: bytes | None, playlist_id: str) -> bool:
    """The publish answer names ``playlist_id`` and says it is public."""
    answer = _object(body)
    return (
        answer is not None
        and answer.get("playlist_id") == playlist_id
        and answer.get("public") is True
    )


def parse_shared(body: bytes | None) -> list[str] | None:
    """The ids on one page of the caller's published playlists, or ``None`` for an answer that is
    not a list of entries (an entry without a valid id is left out)."""
    if body is None:
        return None
    try:
        answer: object = json.loads(body)
    except (ValueError, RecursionError):
        return None
    if not isinstance(answer, list):
        return None
    ids: list[str] = []
    for entry in cast(list[Any], answer):
        if not isinstance(entry, dict):
            return None
        found = cast(dict[str, Any], entry).get("playlist_id")
        if is_playlist_id(found):
            ids.append(cast(str, found))
    return ids


def is_mix_url(value: object) -> TypeGuard[str]:
    """An ``https`` URL with a host and a path, no userinfo, query or fragment, and no space
    (``urlsplit`` drops leading and trailing blanks, so one would pass as another URL)."""
    if not isinstance(value, str) or not 1 <= len(value) <= MAX_URL or not value.isprintable():
        return False
    if any(c.isspace() for c in value):
        return False
    try:
        parts = urlsplit(value)
    except ValueError:
        return False
    return (
        parts.scheme == "https"
        and bool(parts.hostname)
        and "@" not in parts.netloc
        and len(parts.path) > 1
        and not parts.query
        and not parts.fragment
    )


def parse_merged(body: bytes | None) -> str | None:
    """The published mix's URL, or ``None``."""
    answer = _object(body)
    found = None if answer is None else answer.get("url")
    return found if is_mix_url(found) else None


def x_tracks_header(tracks: list[dict[str, Any]]) -> bytes:
    """``tracks`` as the header's UTF-8 JSON; ``ValueError`` when it is empty, holds more than
    200 items or is over ``X_TRACKS_MAX`` bytes."""
    if not 1 <= len(tracks) <= 200:
        raise ValueError("X-Tracks lists 1 to 200 tracks")
    raw = json.dumps(tracks, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if len(raw) > X_TRACKS_MAX:
        raise ValueError("X-Tracks is at most 16 KB")
    return raw
