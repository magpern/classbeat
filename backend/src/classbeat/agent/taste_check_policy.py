"""Does the chosen song list keep to the request (issue #1133; pure: no database, no network)?

Run after ``choose_songs``, before the list is stored. Each miss is a **Not as asked** item
(``interpretation_record.InterpretationItem``) the ``song_list`` keeps in ``unmet`` and the
approval screen words by its code, instead of a list that quietly misses the request:

- ``music_era.N`` ``era_too_few`` (pick mode, a stated era): fewer chosen songs from that era than
  ``MIN_ERA_SHARE`` of the new songs spread over the eras (at least one each); the value is the
  era and how many were found;
- ``music_taste`` ``songs_outside_eras`` (pick mode): new songs from no stated era, with the count;
- ``music_length`` ``songs_too_short`` (both modes): the list is shorter than the class by more
  than ``LENGTH_TOLERANCE`` (the value is both lengths in minutes).

A song's year is its Spotify search hit's release year (``PoolPick.year``); a kept song of a
replacement has none and is not judged, so a replacement is checked for its length only. The
language and the style words are told to the model but cannot be checked here: Spotify's search
answer has neither.
"""

from collections.abc import Mapping, Sequence
from typing import Final, Protocol

from pydantic import JsonValue

from classbeat.agent.interpretation_record import InterpretationItem
from classbeat.agent.music_taste import MusicTaste

LENGTH_TOLERANCE: Final = 0.10  # ``songs.TOLERANCE``, the choice's own (a test keeps them equal)
MIN_ERA_SHARE: Final = 0.5  # of an even spread over the eras
ERA_TOO_FEW: Final = "era_too_few"
OUTSIDE_ERAS: Final = "songs_outside_eras"
TOO_SHORT: Final = "songs_too_short"


class Picked(Protocol):
    @property
    def spotify_id(self) -> str: ...
    @property
    def year(self) -> int | None: ...


class Chosen(Protocol):
    @property
    def spotify_id(self) -> str: ...
    @property
    def new(self) -> bool: ...


class ChosenSongs(Protocol):
    @property
    def songs(self) -> Sequence[Chosen]: ...
    @property
    def mode(self) -> str: ...
    @property
    def total_sec(self) -> int: ...
    @property
    def target_sec(self) -> int: ...


def _miss(path: str, code: str, value: Mapping[str, int]) -> InterpretationItem:
    held: dict[str, JsonValue] = dict(value)
    return InterpretationItem(path=path, origin="not_as_asked", reason_code=code, value=held)


def era_misses(taste: MusicTaste, years: Sequence[int | None]) -> list[InterpretationItem]:
    """The era items for the new songs' ``years`` (see the module)."""
    if not taste.eras or not years:
        return []
    need = max(1, int(len(years) * MIN_ERA_SHARE / len(taste.eras)))
    out: list[InterpretationItem] = []
    for n, era in enumerate(taste.eras):
        found = sum(1 for y in years if era.holds(y))
        if found < need:
            out.append(_miss(f"music_era.{n}", ERA_TOO_FEW, {**era.view(), "found": found}))
    outside = sum(1 for y in years if not taste.in_any_era(y))
    if outside:
        out.append(_miss("music_taste", OUTSIDE_ERAS, {"songs": outside}))
    return out


def length_miss(total_sec: int, target_sec: int) -> list[InterpretationItem]:
    if total_sec >= target_sec * (1 - LENGTH_TOLERANCE):
        return []
    value = {"total_min": round(total_sec / 60), "target_min": round(target_sec / 60)}
    return [_miss("music_length", TOO_SHORT, value)]


def unmet(
    taste: MusicTaste | None,
    picks: Sequence[Picked],
    songs: Sequence[Chosen],
    *,
    mode: str,
    total_sec: int,
    target_sec: int,
) -> list[InterpretationItem]:
    """Every miss of the chosen ``songs`` (their years from the pool ``picks``)."""
    out: list[InterpretationItem] = []
    if taste is not None and mode == "pick":
        year_of: Mapping[str, int | None] = {p.spotify_id: p.year for p in picks}
        out += era_misses(taste, [year_of.get(s.spotify_id) for s in songs if s.new])
    return out + length_miss(total_sec, target_sec)


def list_unmet(
    taste: MusicTaste | None, picks: Sequence[Picked], chosen: ChosenSongs
) -> list[InterpretationItem]:
    """``unmet`` of a ``ChosenList`` (what ``songs.pick`` stores with the list)."""
    return unmet(
        taste,
        picks,
        chosen.songs,
        mode=chosen.mode,
        total_sec=chosen.total_sec,
        target_sec=chosen.target_sec,
    )
