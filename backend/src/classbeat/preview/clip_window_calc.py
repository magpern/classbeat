"""A clip's window in whole milliseconds of class time (ADR 0042 section 6.4). Pure: no I/O.

The browser asks for a clip by its start ``start_ms``; ``preview_window_calc.place_window`` places
the window in output samples (rules 1 to 4), and this module names it in **absolute class time**
``[start_ms, end_ms)``, the cache key's unit, and back in samples for the render:

- ``frames_of(ms)`` is ``floor(ms * 44.1)``: the same function on both sides of a boundary, so the
  next window of a session (it starts at the previous one's ``end_ms``, rule 5) begins on exactly
  the sample where the previous one ended. No sample is lost or doubled at a join.
- A window's end in ms is the placed end rounded **down** to a whole ms (at most 23 µs earlier,
  never into a guard zone it was moved out of by more than that), except at the mix end, where it
  is rounded up and the render's end is the mix's last sample.
- No window is over 6 min: ``end_ms - start_ms <= MAX_WINDOW_MS``, and its samples are at most
  ``render_policy.MAX_WINDOW_FRAMES``.
"""

from collections.abc import Mapping, Sequence
from typing import Any, Final, NamedTuple

from classbeat.studio import preview_window_calc, render_policy

RATE: Final = render_policy.RATE
MAX_WINDOW_MS: Final = render_policy.MAX_WINDOW_FRAMES * 1000 // RATE  # 360000


class ClipWindow(NamedTuple):
    """``[start_ms, end_ms)`` in class time."""

    start_ms: int
    end_ms: int

    @property
    def length_ms(self) -> int:
        return self.end_ms - self.start_ms


def frames_of(ms: int) -> int:
    """The output sample at ``ms`` (rounded down)."""
    return ms * RATE // 1000


def _end_ms(end_frame: int, total: int, start_ms: int) -> int:
    if end_frame >= total:
        ceiling = -(-end_frame * 1000 // RATE)
        return min(ceiling, start_ms + MAX_WINDOW_MS)
    return end_frame * 1000 // RATE


def clip_window(tracks: Sequence[Mapping[str, Any]], start_ms: int) -> ClipWindow:
    """The window that starts at ``start_ms``; ``ValueError`` when that is not inside the mix or
    the window would be empty in whole milliseconds."""
    if isinstance(start_ms, bool) or not isinstance(start_ms, int) or start_ms < 0:  # pyright: ignore[reportUnnecessaryIsInstance]
        raise ValueError("start")
    total = preview_window_calc.total_frames(tracks)
    placed = preview_window_calc.place_window(tracks, frames_of(start_ms))
    window = ClipWindow(start_ms, _end_ms(placed.end, total, start_ms))
    if window.length_ms <= 0:
        raise ValueError("start")
    return window


def render_window(window: ClipWindow, tracks: Sequence[Mapping[str, Any]]) -> tuple[int, int]:
    """The window's ``[start, end)`` in output samples, as the render plan's ``window`` takes it."""
    total = preview_window_calc.total_frames(tracks)
    return frames_of(window.start_ms), min(frames_of(window.end_ms), total)
