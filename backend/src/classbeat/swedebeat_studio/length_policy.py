"""The studio's length checks (ADR 0036 section 3.3, amendment B6). Pure: no I/O.

It runs on the SwedeBeat durations of the match, never on Spotify's or MOWL's. The studio's default
form is **whole songs**, each once, no crossfade, so a list's time is the plain sum of the durations
(``studio_total``). ``within`` is the one class-length check: 10 % under to 10 % over the target
(``UPPER`` is the song pick's ``TOLERANCE``).

The song stage (``choose_songs``, through ``ChooseContext.measure``) accepts a sum ``S`` of ``n``
songs only when it passes every length check the whole-songs form meets later, with a margin of
``DURATION_SLACK_SEC`` per song for a whole-second ``duration_sec`` against the analysed length:
``S >= target * 0.90 + n * slack`` and ``S <= min(target * 1.10, max_draft_seconds(gates),
MAX_TOTAL_SEC) - n * slack`` (``bounds``). A job input with no plan gates has no plan bound.
"""

import math
from collections.abc import Sequence
from typing import Final

from classbeat.agent.length_measure import LengthMeasure
from classbeat.agent.songs import TOLERANCE
from classbeat.plan_gates import PlanGates, max_draft_seconds
from classbeat.studio.mix_plan_policy import MAX_TOTAL_SEC

UPPER: Final = TOLERANCE
# The most a whole-second ``duration_sec`` and the encoder's padding can differ from the analysed
# length of a song, in seconds (the fixtures' ``duration_sec`` is the analysed length, ADR 0036 B6).
DURATION_SLACK_SEC: Final = 1
_EDGE_DIGITS: Final = 6  # float noise of ``target * 0.9`` never moves a whole-second edge


def studio_total(durations: Sequence[int]) -> int:
    """The class time of these SwedeBeat durations as whole songs, whole seconds: the plain sum."""
    return sum(durations)


def within(total: float, target_sec: int) -> bool:
    """The class-length check: 10 % under to 10 % over ``target_sec``."""
    low = round(target_sec * (1 - TOLERANCE), _EDGE_DIGITS)
    high = round(target_sec * (1 + UPPER), _EDGE_DIGITS)
    return low <= total <= high


def bounds(target_sec: int, songs: int, gates: PlanGates | None) -> tuple[float, float]:
    """The song stage's lowest and highest sum for ``songs`` songs (it can be empty: low > high)."""
    margin = songs * DURATION_SLACK_SEC
    ceiling = min(target_sec * (1 + UPPER), MAX_TOTAL_SEC)
    if gates is not None:
        ceiling = min(ceiling, max_draft_seconds(gates))
    low = round(target_sec * (1 - TOLERANCE) + margin, _EDGE_DIGITS)
    return low, round(ceiling - margin, _EDGE_DIGITS)


def unfillable(target_sec: int, songs: int, gates: PlanGates | None) -> bool:
    """No list of ``songs`` songs can pass the song stage (a class over about 165 minutes)."""
    low, high = bounds(target_sec, songs, gates)
    return low > high


def _problem(gates: PlanGates | None, total: float, target_sec: int, songs: int) -> str | None:
    low, high = bounds(target_sec, songs, gates)
    if low <= total <= high:
        return None
    return (
        f"the total time is {round(total)} s (whole songs, no crossfade); "
        f"it must be between {math.ceil(low)} and {math.floor(high)} s"
    )


def whole_songs_measure(gates: PlanGates | None) -> LengthMeasure:
    """The studio's measure for the choose stage: the plain sum, crossfade 0, ``bounds``."""
    return LengthMeasure(
        0.0,
        lambda lengths: float(studio_total(lengths)),
        lambda total, target_sec, songs: _problem(gates, total, target_sec, songs),
    )
