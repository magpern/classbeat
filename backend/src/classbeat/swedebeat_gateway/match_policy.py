"""The match body and answer (ADR 0035 section 6). Pure: no I/O.

- **The request** is ``{"tracks": [{"title", "artist"}], "include_unsure": false}``: only each
  song's title and artist leave (no Spotify id, BPM, length, role, class, brief or user detail).
  Never the gateway's ``playlist`` mode (ADR 0035 section 7). Each title and artist is non-empty
  after trimming and at most 200 characters, and there are 1 to 200 tracks: a value outside the
  bounds would make the gateway refuse the whole request, so it is a ``ValueError`` here (the
  caller leaves such a song out; it is ``missing``).
- **The answer** is read through a field allowlist. ``items`` must be a list with exactly one
  item per sent track, in order; any other count, a non-object item or an unknown ``status`` is
  ``None`` (``invalid_response``). Of each item only ``status``, ``score`` and ``track.reference``,
  ``track.title``, ``track.artist``, ``track.duration`` and ``track.bpm`` are read: the reference
  ``track_policy.REFERENCE`` (ADR 0036 section 3.4), the texts at most 200 characters with
  URL-shaped and credential-shaped values dropped, the duration finite from 0 to 3600 s and
  rounded, the BPM finite from 1 to 400 and rounded to 0.1 (else ``None``), the score a finite
  number from 0 to 1. A track without a valid reference or a usable title and artist is ``None``
  (the song's verdict is ``missing``); so is every ``missing`` item's. ``selected``, the totals,
  ``playlist_name``, every other field and every URL are dropped here, and nothing from the
  answer is ever fetched. The reference is the download's key (``client.download_track``) and
  never reaches a model: a model sees song numbers and statuses only.
"""

import json
import math
import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Final, Literal

from classbeat.swedebeat_gateway.track_policy import is_reference

MATCH_ANSWER_MAX: Final = 1024 * 1024
TRACKS_MAX: Final = 200
MAX_TEXT: Final = 200
MAX_DURATION: Final = 3600
MIN_BPM: Final = 1.0
MAX_BPM: Final = 400.0

Status = Literal["found", "unsure", "missing"]
_STATUSES: Final = frozenset({"found", "unsure", "missing"})
# Values that are no song's title or artist (ADR 0016 section 4's parser rule): a URL or a
# scheme, an e-mail, or a credential's shape (an access key id, a JWT, a long unbroken key run).
_URLISH = re.compile(r"(?i)([a-z][a-z0-9+.-]*://|\bwww\.|\bmailto:|@[a-z0-9-]+\.[a-z])")
_KEY_RUN = r"[0-9A-Za-z+/=_-]"
_CREDENTIALISH = re.compile(
    r"((?:AKIA|ASIA)[0-9A-Z]{12,}|eyJ[0-9A-Za-z_-]{8,}\."
    rf"|(?={_KEY_RUN}*[0-9])(?={_KEY_RUN}*[A-Za-z]){_KEY_RUN}{{24,}})"
)


@dataclass(frozen=True, slots=True)
class Track:
    """A catalogue track as the parser keeps it."""

    title: str
    artist: str
    duration: int | None  # whole seconds, or None when the answer gave no usable length
    reference: str  # track_policy.REFERENCE: the download's key, never a model's input
    bpm: float | None = None  # SwedeBeat's tempo, 1 to 400 rounded to 0.1, or None


@dataclass(frozen=True, slots=True)
class Item:
    """One sent song's answer: the gateway's status, its score (``None`` when unusable) and the
    usable track (``None`` for ``missing`` or a track that failed the checks)."""

    status: Status
    score: float | None
    track: Track | None


def match_body(tracks: Sequence[tuple[str, str]]) -> bytes:
    """The match request's JSON for ``(title, artist)`` pairs, in play order."""
    if not 1 <= len(tracks) <= TRACKS_MAX:
        raise ValueError("a match sends 1 to 200 tracks")
    sent: list[dict[str, str]] = []
    for title, artist in tracks:
        if not (title.strip() and artist.strip()):
            raise ValueError("a sent title and artist are not empty")
        if len(title) > MAX_TEXT or len(artist) > MAX_TEXT:
            raise ValueError("a sent title and artist are at most 200 characters")
        sent.append({"title": title, "artist": artist})
    return json.dumps({"tracks": sent, "include_unsure": False}).encode("ascii")


def _text(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    text = " ".join("".join(c if c.isprintable() else " " for c in value).split())
    if not text or len(text) > MAX_TEXT:
        return None
    if _URLISH.search(text) or _CREDENTIALISH.search(text):
        return None
    return text


def _duration(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    if not math.isfinite(value) or not 0 <= value <= MAX_DURATION:
        return None
    return round(value)


def _bpm(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    if not math.isfinite(value) or not MIN_BPM <= value <= MAX_BPM:
        return None
    return round(float(value), 1)


def _score(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    if not math.isfinite(value) or not 0 <= value <= 1:
        return None
    return float(value)


def _track(raw: object) -> Track | None:
    if not isinstance(raw, dict):
        return None
    node: dict[str, Any] = raw  # pyright: ignore[reportUnknownVariableType]
    title, artist = _text(node.get("title")), _text(node.get("artist"))
    reference: object = node.get("reference")
    if title is None or artist is None or not isinstance(reference, str):
        return None
    if not is_reference(reference):
        return None
    duration, bpm = _duration(node.get("duration")), _bpm(node.get("bpm"))
    return Track(title, artist, duration, reference, bpm)


def _item(raw: object) -> Item | None:
    if not isinstance(raw, dict):
        return None
    node: dict[str, Any] = raw  # pyright: ignore[reportUnknownVariableType]
    status = node.get("status")
    if not isinstance(status, str) or status not in _STATUSES:
        return None
    track = None if status == "missing" else _track(node.get("track"))
    return Item(status, _score(node.get("score")), track)  # pyright: ignore[reportArgumentType]


def parse_match(body: bytes | None, sent: int) -> list[Item] | None:
    """One kept item per sent track, in order, or ``None`` (``invalid_response``)."""
    if body is None or len(body) > MATCH_ANSWER_MAX:
        return None
    try:
        answer = json.loads(body)
    except (ValueError, RecursionError):
        return None
    if not isinstance(answer, dict):
        return None
    raw_items: object = answer.get("items")  # pyright: ignore[reportUnknownMemberType]
    if not isinstance(raw_items, list) or len(raw_items) != sent:  # pyright: ignore[reportUnknownArgumentType]
        return None
    items = [_item(raw) for raw in raw_items]  # pyright: ignore[reportUnknownVariableType]
    if any(item is None for item in items):
        return None
    return [item for item in items if item is not None]
