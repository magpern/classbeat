"""A stored class revision and its picked songs as a ``PlayerClass`` (ADR 0034 sections 4 and 5).

Pure: plain values in, the ``player_format_model`` input out; no database, network or UI. The
song windows are the reference converter's (``tests/player_format/reference_converter.py``):
each song starts ``crossfade`` before the previous one ends, the last window ends at the class's
end. Whether the windows fit is ``player_format_policy``'s refusal, never decided here.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from classbeat.player_format_model import (
    MusicSource,
    PlayerClass,
    PlayerSong,
    Sport,
    class_length_ms,
)


@dataclass(frozen=True, slots=True)
class PickedSong:
    """One song of an approved song list, as stored."""

    title: str
    artist: str
    length_sec: int
    bpm: float | None
    spotify_id: str | None


def milliseconds(seconds: float) -> int:
    return round(seconds * 1000)


def song_windows(
    songs: Sequence[PickedSong], crossfade_ms: int, class_ms: int
) -> tuple[PlayerSong, ...]:
    """The windows of ``songs`` in play order over a class of ``class_ms``."""
    out: list[PlayerSong] = []
    start = 0
    for index, song in enumerate(songs):
        length = milliseconds(song.length_sec)
        end = class_ms if index == len(songs) - 1 else start + length - crossfade_ms
        out.append(
            PlayerSong(
                title=song.title,
                artist=song.artist,
                length_ms=length,
                start_ms=start,
                end_ms=end,
                bpm=song.bpm,
                spotify_track=song.spotify_id,
            )
        )
        start = end
    return tuple(out)


def player_class(
    draft: Mapping[str, Any],
    sport: Sport,
    music_source: MusicSource,
    crossfade_sec: float,
    songs: Sequence[PickedSong],
) -> PlayerClass:
    """The class to convert. A soundless class has no crossfade and no songs."""
    if music_source == "none":
        return PlayerClass(draft, sport, music_source, 0, ())
    crossfade_ms = milliseconds(crossfade_sec)
    windows = song_windows(songs, crossfade_ms, class_length_ms(draft))
    return PlayerClass(draft, sport, music_source, crossfade_ms, windows)
