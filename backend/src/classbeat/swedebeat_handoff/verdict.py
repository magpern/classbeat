"""The length verdict of a built Swedebeat mix (ADR 0016 section 7).

The mix's track list cannot be read. The analysed length should sit near ``swedebeat_sec`` minus
up to 10 seconds per change between found songs (a crossfade), plus a small tolerance:

- low = ``swedebeat_sec - 10 * changes - TOLERANCE_SECONDS``
- high = ``swedebeat_sec + TOLERANCE_SECONDS``

``ok`` is inside that band. Below it is ``too_short`` and names the found-song positions that do
not fit, walking the cumulative length with the same 10 second overlap. Above it is ``too_long``.
``not_found`` is not a length verdict: the listing showed no new mix.
"""

from dataclasses import dataclass
from typing import Final, Literal

from classbeat.swedebeat_match.revision import MatchedSong, found_seconds

CHANGE_SECONDS: Final = 10
TOLERANCE_SECONDS: Final = 15

VerdictName = Literal["ok", "too_short", "too_long"]


@dataclass(frozen=True, slots=True)
class LengthVerdict:
    verdict: VerdictName
    analysed_sec: float
    expected_low_sec: int
    expected_high_sec: int
    missing_positions: tuple[int, ...]


def bounds(swedebeat_sec: int, found_count: int) -> tuple[int, int]:
    """The inclusive band an analysed length is ``ok`` inside."""
    changes = max(found_count - 1, 0)
    low = max(0, swedebeat_sec - CHANGE_SECONDS * changes - TOLERANCE_SECONDS)
    high = swedebeat_sec + TOLERANCE_SECONDS
    return low, high


def _found(songs: list[MatchedSong]) -> list[MatchedSong]:
    return [
        song
        for song in songs
        if song.status == "found" and song.swedebeat is not None and song.swedebeat.duration
    ]


def likely_missing(songs: list[MatchedSong], analysed_sec: float) -> tuple[int, ...]:
    """Found positions that do not fit in ``analysed_sec``, in play order.

    Each found song starts where the previous one ends, minus 10 seconds of overlap. A song
    whose start is already at the end of the mix, or which is more than half past it, is named.
    When the length is short but every song still starts inside the mix, the last found song
    is named.
    """
    found = _found(songs)
    missing: list[int] = []
    cursor = 0.0
    for index, song in enumerate(found):
        start = cursor
        hit = song.swedebeat
        duration = float(hit.duration) if hit is not None and hit.duration is not None else 0.0
        past_the_start = start >= analysed_sec
        mostly_past = analysed_sec - start < duration / 2
        if past_the_start or (mostly_past and index > 0):
            missing.append(song.position)
        cursor += duration
        if index < len(found) - 1:
            cursor -= CHANGE_SECONDS
    if not missing and found:
        missing.append(found[-1].position)
    return tuple(missing)


def judge(songs: list[MatchedSong], analysed_sec: float) -> LengthVerdict:
    """``ok``, ``too_short`` or ``too_long`` for this analysed length."""
    found = _found(songs)
    low, high = bounds(found_seconds(songs), len(found))
    if analysed_sec > high:
        name: VerdictName = "too_long"
        positions: tuple[int, ...] = ()
    elif analysed_sec < low:
        name = "too_short"
        positions = likely_missing(songs, analysed_sec)
    else:
        name = "ok"
        positions = ()
    return LengthVerdict(name, analysed_sec, low, high, positions)
