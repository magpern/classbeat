"""How the ``choose_songs`` stage measures a song list's length. Pure: no I/O.

A ``LengthMeasure`` is the total function, the crossfade shown to the model and stored with the
list, and the bounds check with its model-facing error text. The default (``SONG_MEASURE``) is
the Spotify pick's: lengths less 9.54 s per change of song (``songs.total_sec``) within 10 % of the
target (``songs.within_tolerance``). The studio passes its own (``swedebeat_studio.length_policy``,
ADR 0036 amendment B, B6): whole songs, no crossfade.
"""

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Final

from classbeat.agent.songs import CROSSFADE_SEC, TOLERANCE, total_sec, within_tolerance


@dataclass(frozen=True, slots=True)
class LengthMeasure:
    crossfade_sec: float
    total: Callable[[Sequence[int]], float]
    # (total, target_sec, number of songs) -> the error for the model, or None when it fits.
    problem: Callable[[float, int, int], str | None]


def _song_problem(total: float, target_sec: int, count: int) -> str | None:
    if within_tolerance(total, target_sec):
        return None
    low, high = round(target_sec * (1 - TOLERANCE)), round(target_sec * (1 + TOLERANCE))
    return (
        f"the total time is {round(total)} s (lengths less {CROSSFADE_SEC:g} s per change of "
        f"song); it must be between {low} and {high} s"
    )


SONG_MEASURE: Final = LengthMeasure(CROSSFADE_SEC, total_sec, _song_problem)
