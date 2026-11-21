"""Swaps, undo and gaps of a studio song list (ADR 0036 section 3.3). Pure: no I/O.

- The candidates and the swap records are ``candidate_policy``'s.
- **Undo** (``undo``): the swapped slot becomes the original again, as a **gap** (no SwedeBeat
  facts, ``swap.undone``). The other songs stay as they are.
- **Another** (``another_slots``, ``expand``): the songs around the asked slot are kept in their
  slots for the choose stage, other gaps left out of it and put back afterwards.
- **A gap never goes into a mix** (``mix_tracks``): a list with a gap, or a suspected list, has no
  mix tracks, only ``GapInMix``.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from classbeat.agent.songs import KeptSong
from classbeat.swedebeat_studio.length_policy import studio_total
from classbeat.swedebeat_studio.song_list_model import (
    Facts,
    Original,
    StudioListContent,
    StudioSong,
)


class GapInMix(ValueError):
    """The list holds a gap (or is suspected): nothing of it may be mixed or approved."""


def song_total(songs: Sequence[StudioSong]) -> int:
    """The class time of the list's non-gap songs: whole songs, the sum of their durations."""
    return studio_total([s.swedebeat.duration_sec for s in songs if s.swedebeat is not None])


def with_songs(content: StudioListContent, songs: Sequence[StudioSong]) -> StudioListContent:
    return content.model_copy(
        update={"songs": list(songs), "total_sec": song_total(songs), "crossfade_sec": 0.0}
    )


def undoable(content: StudioListContent, position: int) -> bool:
    if not 0 <= position < len(content.songs):
        return False
    swap = content.songs[position].swap
    return swap is not None and not swap.undone


def undo(content: StudioListContent, position: int) -> StudioListContent:
    """The list with the swap at ``position`` undone: its original back, as a gap."""
    if not undoable(content, position):
        raise ValueError("no swap to undo at that position")
    song = content.songs[position]
    swap = song.swap
    assert swap is not None  # noqa: S101 - undoable
    original = swap.original
    gap = StudioSong(
        spotify_id=original.spotify_id,
        title=original.title,
        artist=original.artist,
        role=song.role,
        new=song.new,
        cover=original.cover,
        gap=True,
        swedebeat=None,
        swap=swap.model_copy(update={"undone": True}),
    )
    songs = list(content.songs)
    songs[position] = gap
    return with_songs(content, songs)


def askable(content: StudioListContent, position: int) -> bool:
    """Whether "another song" may be asked for ``position``: a swapped slot or a gap."""
    if not 0 <= position < len(content.songs) or content.evidence.state != "ok":
        return False
    song = content.songs[position]
    return song.gap or song.swap is not None


@dataclass(frozen=True)
class AnotherSlots:
    """The choose stage's view of an "another" request: the slot to fill and the kept songs in
    the list without the other gaps (``slot`` and each ``KeptSong.slot`` count in that list)."""

    slot: int
    role: str
    kept: tuple[KeptSong, ...]
    gaps: Mapping[int, StudioSong]  # the other gaps, by their position in the stored list


def another_slots(content: StudioListContent, position: int) -> AnotherSlots:
    if not askable(content, position):
        raise ValueError("no song to ask another for at that position")
    kept: list[KeptSong] = []
    gaps: dict[int, StudioSong] = {}
    slot = 0
    for at, song in enumerate(content.songs):
        if at == position:
            slot = at - len(gaps)
            continue
        if song.swedebeat is None:
            gaps[at] = song
            continue
        kept.append(
            KeptSong(
                slot=at - len(gaps),
                spotify_id=song.spotify_id,
                title=song.title,
                artist=song.artist,
                role=song.role,
                bpm=song.swedebeat.bpm,
                length_sec=song.swedebeat.duration_sec,
                cover=song.cover,
            )
        )
    return AnotherSlots(slot, content.songs[position].role, tuple(kept), gaps)


def expand(chosen: Sequence[StudioSong], gaps: Mapping[int, StudioSong]) -> list[StudioSong]:
    """The chosen list with the other gaps back in their positions."""
    songs = list(chosen)
    for at in sorted(gaps):
        songs.insert(at, gaps[at])
    return songs


def asked_original(song: StudioSong) -> Original:
    """What "another" replaces, as the new swap names it: the first original, if any."""
    if song.swap is not None:
        return song.swap.original
    return Original(
        spotify_id=song.spotify_id, title=song.title, artist=song.artist, cover=song.cover
    )


def mix_tracks(content: StudioListContent) -> list[Facts]:
    """The SwedeBeat tracks a mix may use, in order. ``GapInMix`` for a list with a gap or a
    suspected one: such a list can never be approved into a mix."""
    if content.evidence.state != "ok" or content.has_gap():
        raise GapInMix("the list has a gap")
    return [s.swedebeat for s in content.songs if s.swedebeat is not None]
