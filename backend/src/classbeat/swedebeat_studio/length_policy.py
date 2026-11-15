"""The studio's length check (ADR 0036 section 3.3). Pure: no I/O.

It runs on the SwedeBeat durations of the match, never on Spotify's or MOWL's. The total is the
songs' durations less the crossfade per change of song (``agent.songs.total_sec``, 9 s each until
the mix spec sets the real crossfades). The lower bound is the song pick's (10 % under the
target); the upper bound is relaxed to ``UPPER`` (50 % over), because the designed cuts can only
shorten a track. The choose stage checks the same bounds (``ChooseContext.upper``).
"""

from collections.abc import Sequence
from typing import Final

from classbeat.agent.songs import total_sec, within_tolerance

UPPER: Final = 0.50


def studio_total(durations: Sequence[int]) -> int:
    """The class time of these SwedeBeat durations played in a row, whole seconds."""
    return round(total_sec(durations))


def within(total: float, target_sec: int) -> bool:
    return within_tolerance(total, target_sec, upper=UPPER)
