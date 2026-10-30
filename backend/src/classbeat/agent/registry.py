"""The per-job search registry (ADR 0008 section 3; threat model T54, T69).

``search_tracks`` answers the model with a ``ref`` per track: an index into this registry, which
maps it to the Spotify track id. **The id never reaches the model**: not in a result, not in an
error, not in a prompt (``Entry.spotify_id`` is the backend's alone, and has no ``repr``). The
registry lives in memory for one pick job (at most ``MAX_ENTRIES`` tracks) and is never stored;
the model can only name a track it was shown, and only by its ref.

What it keeps of a track is the cleaned, cut text the model was shown (control and format
characters removed, 120 characters), its length in whole seconds and its year. The same Spotify
id found by two searches is one entry with one ref.
"""

import re
import unicodedata
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Final

from classbeat.agent.schemas import clean_text

MAX_ENTRIES: Final = 300
MAX_TEXT: Final = 120
SPOTIFY_ID: Final = re.compile(r"[0-9A-Za-z]{22}")
MIN_SEC: Final = 30  # shorter than a song: a jingle or an intro
MAX_SEC: Final = 1200  # longer than a song: a mix or a podcast


@dataclass(frozen=True, slots=True)
class Entry:
    ref: int
    title: str
    artist: str
    album: str
    year: int | None
    duration_sec: int
    spotify_id: str = field(repr=False)

    def key(self) -> tuple[str, str]:
        return normalised(self.title), normalised(self.artist)

    def shown(self) -> dict[str, Any]:
        """The one shape the model sees (``ref`` and text; never the id)."""
        return {
            "ref": self.ref,
            "title": self.title,
            "artist": self.artist,
            "album": self.album,
            "year": self.year,
            "duration_sec": self.duration_sec,
        }


def normalised(text: str) -> str:
    """Case, accents, punctuation and spacing removed, for the duplicate check."""
    folded = unicodedata.normalize("NFKD", text).casefold()
    return "".join(c for c in folded if c.isalnum())


def _text(value: Any) -> str:
    return clean_text(value)[:MAX_TEXT].strip() if isinstance(value, str) else ""


def _year(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    text = str(value) if isinstance(value, int | str) else ""
    return int(text) if re.fullmatch(r"[12]\d{3}", text) else None


class SearchRegistry:
    def __init__(self, max_entries: int = MAX_ENTRIES) -> None:
        self._max = max_entries
        self._entries: list[Entry] = []
        self._by_id: dict[str, int] = {}
        self.full = False  # a new track was refused for lack of room

    def __len__(self) -> int:
        return len(self._entries)

    def __repr__(self) -> str:  # counts only: nothing of the tracks in a log or a traceback
        return f"SearchRegistry({len(self._entries)} tracks)"

    def get(self, ref: int) -> Entry | None:
        if isinstance(ref, bool) or not isinstance(ref, int) or not 0 <= ref < len(self._entries):
            return None
        return self._entries[ref]

    def spotify_id(self, ref: int) -> str | None:
        """For the backend (the pool write, the stored song list). Never for a model."""
        entry = self.get(ref)
        return entry.spotify_id if entry else None

    def entries(self) -> Sequence[Entry]:
        return tuple(self._entries)

    def add_hits(self, hits: Sequence[Mapping[str, Any]]) -> list[Entry]:
        """Register the library's track dicts (``spotify_web.search_tracks``) and return their
        entries in order. A hit that is not a playable track of song length (no valid id, no or
        absurd length, no visible title) is skipped. A new track that does not fit (the registry
        holds ``MAX_ENTRIES``) is left out and sets ``full``."""
        out: list[Entry] = []
        for hit in hits:
            entry = self._one(hit)
            if entry is not None and entry.ref not in {e.ref for e in out}:
                out.append(entry)
        return out

    def _one(self, hit: Mapping[str, Any]) -> Entry | None:
        tid = hit.get("id")
        if not isinstance(tid, str) or not SPOTIFY_ID.fullmatch(tid):
            return None
        known = self._by_id.get(tid)
        if known is not None:
            return self._entries[known]
        sec = hit.get("sec")
        if isinstance(sec, bool) or not isinstance(sec, int) or not MIN_SEC <= sec <= MAX_SEC:
            return None
        title = _text(hit.get("title"))
        artists = hit.get("artists")
        artist = _text(", ".join(a for a in artists if isinstance(a, str))) if artists else ""
        if not title or not artist:
            return None
        if len(self._entries) >= self._max:
            self.full = True
            return None
        entry = Entry(
            ref=len(self._entries),
            title=title,
            artist=artist,
            album=_text(hit.get("album")),
            year=_year(hit.get("year")),
            duration_sec=sec,
            spotify_id=tid,
        )
        self._entries.append(entry)
        self._by_id[tid] = entry.ref
        return entry
