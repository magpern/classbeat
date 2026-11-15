"""The user's own Spotify playlist as a studio pick's candidates (ADR 0036 section 3.1). Pure: no
I/O.

- The entries are the library's ``playlist_items`` answer, read only: the user's playlist is
  never modified (the working copy is classbeat's pool playlist).
- A candidate is a playable track with a valid id, a visible title and an artist, once per id,
  in the playlist's order, at most ``MAX_POOL``. Its cover is the entry's album image, kept only
  when ``covers.cover_url`` accepts it.
- A playlist names no roles. The position gives a hint the choose stage may change: the first song
  ``warmup``, the last ``cooldown``, the rest ``build``. So a playlist pick swaps only when its
  usable songs cannot fill the list (``PLAYLIST_NEED``: no role is required of the user's songs).
"""

import re
from collections.abc import Mapping, Sequence
from typing import Any, Final

from classbeat.agent.schemas import MAX_POOL, clean_text
from classbeat.covers import cover_url
from classbeat.swedebeat_studio.candidate_policy import Candidate

MAX_TEXT: Final = 200
SPOTIFY_ID: Final = re.compile(r"[0-9A-Za-z]{22}")
PLAYLIST_NEED: Final[Mapping[str, int]] = {}


def _text(value: object) -> str:
    return clean_text(value)[:MAX_TEXT].strip() if isinstance(value, str) else ""


def _role(position: int, count: int) -> str:
    if position == 0:
        return "warmup"
    return "cooldown" if position == count - 1 else "build"


def playlist_candidates(entries: Sequence[Any]) -> list[Candidate]:
    """The candidates of a playlist answer (see the module docstring)."""
    kept: list[tuple[str, str, str, str | None]] = []
    seen: set[str] = set()
    for entry in entries:
        if not isinstance(entry, Mapping) or len(kept) >= MAX_POOL:
            continue
        item: Mapping[str, Any] = entry  # pyright: ignore[reportUnknownVariableType]
        track_id = item.get("id")
        if item.get("playable") is not True or item.get("type") != "track":
            continue
        if not isinstance(track_id, str) or not SPOTIFY_ID.fullmatch(track_id):
            continue
        artists = item.get("artists")
        names = [a for a in artists if isinstance(a, str)] if isinstance(artists, list) else []  # pyright: ignore[reportUnknownVariableType]
        title, artist = _text(item.get("name")), _text(", ".join(names))
        if not title or not artist or track_id in seen:
            continue
        seen.add(track_id)
        kept.append((track_id, title, artist, cover_url(item.get("image"))))
    count = len(kept)
    return [
        Candidate(tid, title, artist, _role(at, count), cover)
        for at, (tid, title, artist, cover) in enumerate(kept)
    ]
