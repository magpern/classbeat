"""The request bodies and paths of spotdown's v2 contract, built only from checked values.
Pure: no I/O.

- ``match_body``: ``POST /v2/match``, 1 to ``MATCH_BATCH_MAX`` (50) tracks, each the metadata
  only: ``client_ref``, ``title``, ``artists``, ``duration_s`` and an optional ``isrc``. Never a
  user token, a URL, a file name or an owner id (ADR 0040 section 1, threat model T131).
- ``analysis_body``: ``POST /v2/analyses`` with **exactly one** of ``upload_id`` or
  ``source_ref``. classbeat never sends spotdown's ``url`` source (a MOWL temporary link never
  leaves classbeat), so there is no parameter for it.
- ``job_path``, ``analysis_path`` and ``scope_path``: an id or a scope from an answer or a caller
  goes into a path only when it matches the contract's pattern, so no answer can steer a request
  to another path (``..``, ``/``, ``?`` are refused).
"""

import json
import math
import re
from dataclasses import dataclass
from typing import Final

from classbeat.spotdown.scope_calc import SCOPE_PATTERN

MATCH_BATCH_MAX: Final = 50
REF_MAX: Final = 128  # client_ref, as spotdown's
TEXT_MAX: Final = 300  # a title or an artist name
ARTISTS_MAX: Final = 10
SOURCE_REF_MAX: Final = 256
ID_PATTERN: Final = re.compile(r"[A-Za-z0-9_-]{1,64}")  # spotdown's ``ID_PATTERN``
_ISRC: Final = re.compile(r"[A-Z]{2}[A-Z0-9]{3}[0-9]{7}")
_SOURCE_REF: Final = re.compile(r"[a-z]+:[A-Za-z0-9_-]{1,200}")
_REFERENCE_KIND: Final = re.compile(r"[a-z]{1,64}")


class RequestRefused(ValueError):
    """A value the contract does not allow; the message names the rule, never the value."""


@dataclass(frozen=True, slots=True)
class TrackQuery:
    """One track's metadata for ``/v2/match``."""

    client_ref: str
    title: str
    artists: tuple[str, ...]
    duration_s: float
    isrc: str | None = None


@dataclass(frozen=True, slots=True)
class Reference:
    """The reference timeline an alignment is requested against (``kind`` is an open set)."""

    kind: str
    id: str
    duration_s: float


def _text(value: object, limit: int, rule: str) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise RequestRefused(rule)
    if not value.isprintable():
        raise RequestRefused(rule)
    return value


def _duration(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise RequestRefused("a duration is a number of seconds")
    if not math.isfinite(value) or not 0 <= value <= 24 * 3600:
        raise RequestRefused("a duration is 0 to 86400 seconds")
    return float(value)


def check_scope(scope: str) -> str:
    """``scope`` when it is a contract ``cache_scope``; ``RequestRefused`` otherwise."""
    if not isinstance(scope, str) or not SCOPE_PATTERN.fullmatch(scope):  # pyright: ignore[reportUnnecessaryIsInstance]
        raise RequestRefused("a cache_scope is 1 to 128 of [A-Za-z0-9_-]")
    return scope


def check_id(value: str) -> str:
    """An upload, job or analysis id that may go into a path or a body."""
    if not isinstance(value, str) or not ID_PATTERN.fullmatch(value):  # pyright: ignore[reportUnnecessaryIsInstance]
        raise RequestRefused("an id is 1 to 64 of [A-Za-z0-9_-]")
    return value


def check_source_ref(value: str) -> str:
    """``<kind>:<ref>`` with a lowercase kind and an id-like ref (a YouTube video id)."""
    if not isinstance(value, str) or not _SOURCE_REF.fullmatch(value):  # pyright: ignore[reportUnnecessaryIsInstance]
        raise RequestRefused("a source_ref is <kind>:<ref>")
    return value


def _track(track: TrackQuery) -> dict[str, object]:
    item: dict[str, object] = {
        "client_ref": _text(track.client_ref, REF_MAX, "a client_ref is 1 to 128 characters"),
        "title": _text(track.title, TEXT_MAX, "a title is 1 to 300 characters"),
    }
    artists = track.artists
    if not isinstance(artists, tuple) or not 1 <= len(artists) <= ARTISTS_MAX:  # pyright: ignore[reportUnnecessaryIsInstance]
        raise RequestRefused("a track has 1 to 10 artists")
    item["artists"] = [_text(a, TEXT_MAX, "an artist is 1 to 300 characters") for a in artists]
    item["duration_s"] = _duration(track.duration_s)
    if track.isrc is not None:
        if not isinstance(track.isrc, str) or not _ISRC.fullmatch(track.isrc):  # pyright: ignore[reportUnnecessaryIsInstance]
            raise RequestRefused("an isrc is 12 characters")
        item["isrc"] = track.isrc
    return item


def _dump(body: dict[str, object]) -> bytes:
    return json.dumps(body, ensure_ascii=True, allow_nan=False, separators=(",", ":")).encode()


def match_body(tracks: tuple[TrackQuery, ...], cache_scope: str | None) -> bytes:
    """``POST /v2/match``'s body. ``client_ref`` values are unique (the answer is matched back)."""
    if not 1 <= len(tracks) <= MATCH_BATCH_MAX:
        raise RequestRefused("a match batch has 1 to 50 tracks")
    items = [_track(t) for t in tracks]
    if len({i["client_ref"] for i in items}) != len(items):
        raise RequestRefused("client_ref values are unique in a batch")
    body: dict[str, object] = {"tracks": items}
    if cache_scope is not None:
        body["cache_scope"] = check_scope(cache_scope)
    return _dump(body)


def analysis_body(
    cache_scope: str,
    client_ref: str,
    *,
    upload_id: str | None = None,
    source_ref: str | None = None,
    reference: Reference | None = None,
) -> bytes:
    """``POST /v2/analyses``'s body: exactly one of ``upload_id`` and ``source_ref``."""
    if (upload_id is None) == (source_ref is None):
        raise RequestRefused("an analysis has exactly one of upload_id and source_ref")
    body: dict[str, object] = {
        "cache_scope": check_scope(cache_scope),
        "client_ref": _text(client_ref, REF_MAX, "a client_ref is 1 to 128 characters"),
    }
    if upload_id is not None:
        body["upload_id"] = check_id(upload_id)
    if source_ref is not None:
        body["source_ref"] = check_source_ref(source_ref)
    if reference is not None:
        if not isinstance(reference.kind, str) or not _REFERENCE_KIND.fullmatch(reference.kind):  # pyright: ignore[reportUnnecessaryIsInstance]
            raise RequestRefused("a reference kind is 1 to 64 lowercase letters")
        body["reference"] = {
            "kind": reference.kind,
            "id": _text(reference.id, SOURCE_REF_MAX, "a reference id is 1 to 256 characters"),
            "duration_s": _duration(reference.duration_s),
        }
    return _dump(body)


def job_path(job_id: str) -> str:
    return f"/v2/jobs/{check_id(job_id)}"


def analysis_path(analysis_id: str) -> str:
    return f"/v2/analyses/{check_id(analysis_id)}"


def scope_path(cache_scope: str) -> str:
    return f"/v2/scopes/{check_scope(cache_scope)}"


_SHA256: Final = re.compile(r"[0-9a-f]{64}")


def check_sha256(value: str) -> str:
    """A lowercase hex sha256 digest."""
    if not isinstance(value, str) or not _SHA256.fullmatch(value):  # pyright: ignore[reportUnnecessaryIsInstance]
        raise RequestRefused("a sha256 is 64 lowercase hex characters")
    return value


def source_lookup_params(source_ref: str, cache_scope: str | None) -> dict[str, str]:
    """``GET /v2/analyses/lookup``'s query by source reference (scope optional: a global source)."""
    params = {"source": check_source_ref(source_ref)}
    if cache_scope is not None:
        params["cache_scope"] = check_scope(cache_scope)
    return params


def content_lookup_params(sha256: str, cache_scope: str) -> dict[str, str]:
    """``GET /v2/analyses/lookup``'s query by content (the scope is required)."""
    return {"sha256": check_sha256(sha256), "cache_scope": check_scope(cache_scope)}
