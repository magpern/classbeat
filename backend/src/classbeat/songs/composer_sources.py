"""The fill's pool sources besides search (ADR 0038 section 5, #1188): the attached Spotify
playlists and the pinned songs, both read through the job's own Spotify connection, **read only**.

- **Attached playlists** (``read_sources``): each playlist's items are read with
  ``spotify_web.playlist_items`` through ``playlists.common.call`` for the connection the job
  opened (the user's own token; Spotify answers items only for a playlist the user owns or
  collaborates on). Nothing is ever written to them (roadmap Amendment 26). A playlist that
  connection cannot read (Spotify answers 4xx) is the Not as asked item ``music_sources.<i>``
  ``source_unreadable`` and the others still count; any other failure ends the job with its
  category (a transient one is retried). The tracks become search-registry hits, **favoured**, at
  most ``MAX_FAVOURED`` in all, in playlist order, each once.
- **Pinned songs** (``verify_pins``): each pin is searched by its title (and artist when the
  reading has one), with the ``find_songs`` rules (``track:"..." artist:"..."``, the same song,
  not a live or cover version, ``song_match_policy``). The first hit the registry accepts is the
  pin; none is the Not as asked item ``pinned_songs.<i>`` ``pinned_not_found``. Neither item is a
  failure.

Nothing here reaches a model but registry text, and nothing is logged.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Final

from mowl import spotify_web

from classbeat.agent.find_songs import WantedSong, song_query
from classbeat.agent.interpretation_record import InterpretationItem
from classbeat.agent.pick_pool import SearchUnavailable
from classbeat.agent.registry import Entry, SearchRegistry, normalised
from classbeat.agent.schemas import SEARCH_DROPPED, clean_text
from classbeat.agent.song_match_policy import matching_hits
from classbeat.connections import ConnectionFailure, OpenConnection
from classbeat.crypto.keks import KeyRing
from classbeat.design.interpret_composer_policy import PinnedSong
from classbeat.design.music_sources import MusicSource
from classbeat.jobs import JobFailure, JobRun
from classbeat.playlists import common
from classbeat.songs.search import job_searcher

MAX_FAVOURED: Final = 60
MAX_ITEMS_READ: Final = 200  # per attached playlist
SOURCE_UNREADABLE: Final = "source_unreadable"
PINNED_NOT_FOUND: Final = "pinned_not_found"


def _unmet(path: str, code: str, value: Any = None) -> InterpretationItem:
    return InterpretationItem(path=path, origin="not_as_asked", reason_code=code, value=value)


def _hit(entry: Mapping[str, Any]) -> dict[str, Any] | None:
    """A playlist entry (``spotify_web.playlist_items``) as a search hit, or None for a marker."""
    if entry.get("playable") is not True or entry.get("type") != "track":
        return None
    ms = entry.get("duration_ms")
    seconds = round(ms / 1000) if isinstance(ms, int) and not isinstance(ms, bool) else None
    release = entry.get("release_date")
    return {
        "id": entry.get("id"),
        "title": entry.get("name"),
        "artists": entry.get("artists") or [],
        "album": entry.get("album"),
        "year": release[:4] if isinstance(release, str) else None,
        "sec": seconds,
        "image": entry.get("image"),
    }


@dataclass(frozen=True)
class Favoured:
    entries: tuple[Entry, ...]
    unmet: tuple[InterpretationItem, ...]


async def read_sources(
    run: JobRun,
    keyring: KeyRing,
    opened: OpenConnection,
    sources: Sequence[MusicSource],
    registry: SearchRegistry,
) -> Favoured:
    """The attached playlists' tracks, registered (module docstring)."""
    found: list[Entry] = []
    unmet: list[InterpretationItem] = []
    for at, source in enumerate(sources):
        playlist_id = common.check_playlist_id(source.id)
        try:
            items = await common.call(
                run.factory,
                keyring,
                run.ctx,
                opened,
                lambda c, pid=playlist_id: spotify_web.playlist_items(c, pid, MAX_ITEMS_READ),
            )
        except ConnectionFailure as failure:
            status = failure.http_status
            if status is not None and 400 <= status <= 499 and status != 429:
                unmet.append(_unmet(f"music_sources.{at}", SOURCE_UNREADABLE))
                continue
            raise
        hits = [h for e in items if isinstance(e, Mapping) and (h := _hit(e)) is not None]
        for entry in registry.add_hits(hits):
            if len(found) >= MAX_FAVOURED:
                break
            if entry.spotify_id not in {f.spotify_id for f in found}:
                found.append(entry)
    return Favoured(tuple(found), tuple(unmet))


def _query(pin: PinnedSong) -> str:
    if pin.artist:
        return song_query(WantedSong(artist=pin.artist, track=pin.title), None)
    words = clean_text(pin.title).translate(SEARCH_DROPPED).split()
    return f'track:"{" ".join(words)}"'


def _title_hits(title: str, hits: Sequence[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    wanted = normalised(title)
    return [h for h in hits if isinstance(h.get("title"), str) and normalised(h["title"]) == wanted]


@dataclass(frozen=True)
class Pinned:
    found: tuple[tuple[int, Entry], ...]  # (the pin's index, its registry entry)
    unmet: tuple[InterpretationItem, ...]


async def verify_pins(
    run: JobRun,
    keyring: KeyRing,
    opened: OpenConnection,
    pins: Sequence[PinnedSong],
    registry: SearchRegistry,
) -> Pinned:
    """Each pin verified by one Spotify search (module docstring)."""
    search = job_searcher(run, keyring, opened)
    found: list[tuple[int, Entry]] = []
    unmet: list[InterpretationItem] = []
    for at, pin in enumerate(pins):
        try:
            found_hits = search(_query(pin))
            hits = list(found_hits if isinstance(found_hits, Sequence) else await found_hits)
        except ValueError:  # no visible words to search for
            hits = []
        except SearchUnavailable:
            raise JobFailure("upstream_unavailable") from None  # retried with backoff
        if pin.artist:
            matched = matching_hits(pin.artist, pin.title, hits, None)
        else:
            matched = _title_hits(pin.title, hits)
        entry = None
        for hit in matched:
            entries = registry.add_hits([hit])
            if entries:
                entry = entries[0]
                break
        if entry is None or entry.spotify_id in {e.spotify_id for _, e in found}:
            unmet.append(_unmet(f"pinned_songs.{at}", PINNED_NOT_FOUND, {"song": at}))
        else:
            found.append((at, entry))
    return Pinned(tuple(found), tuple(unmet))
