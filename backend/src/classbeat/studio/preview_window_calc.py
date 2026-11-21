"""Where a preview window goes in the mix (ADR 0042 section 6.4, placement). Pure: no I/O.

Everything is a whole number of samples at ``render_policy.RATE`` in output time (the render plan's
own unit); a window is ``[start, end)``. The input is the mix's tracks (the stored render plan's
entries or the child's: ``dst_start``, ``src_start``, ``src_end``, ``fade_in``, ``fade_out``) and a
start ``p``: Play or a seek gives the current position, **Review transition** gives
``review_start(transition)``, the fade start less 15 s. The rules:

1. The nominal window is ``[p, p + 4 min)``.
2. A selected transition is **whole** inside the window, plus 15 s after its fade end.
3. Ends avoid transitions by **extension, never by shortening**. The guard zones are each
   transition (a crossfade, a hard cut, a gap) plus and minus 15 s; touching zones chain. An end
   strictly inside a zone moves **later**, to the zone's end, up to 6 min (``MAX_WINDOW_FRAMES``).
   If 6 min still falls inside a zone, the window ends at 6 min: that join may fall in a
   transition, never in the selected one.
4. No window is shorter than 60 s, except at the mix end.
5. The next window of a session starts at the previous one's end (``next_window``).
"""

from collections.abc import Iterator, Mapping, Sequence
from itertools import pairwise
from typing import Any, Final, NamedTuple

from classbeat.studio import render_policy

RATE: Final = render_policy.RATE
NOMINAL_FRAMES: Final = 4 * 60 * RATE
GUARD_FRAMES: Final = 15 * RATE
MIN_FRAMES: Final = 60 * RATE
MAX_FRAMES: Final = render_policy.MAX_WINDOW_FRAMES


class Window(NamedTuple):
    """``[start, end)`` in output samples."""

    start: int
    end: int


class Transition(NamedTuple):
    """The span ``[start, end)`` in which the mix changes from one track to the next: where the
    fades and the overlap lie, or the gap between them; ``end == start`` for a hard cut."""

    start: int
    end: int


def _end_of(track: Mapping[str, Any]) -> int:
    return track["dst_start"] + track["src_end"] - track["src_start"]


def total_frames(tracks: Sequence[Mapping[str, Any]]) -> int:
    """The length of the mix in samples: where the last track ends."""
    return max(_end_of(t) for t in tracks)


def transitions(tracks: Sequence[Mapping[str, Any]]) -> list[Transition]:
    """One transition per change of track, in mix order."""
    found: list[Transition] = []
    for before, after in pairwise(tracks):
        leaves = _end_of(before) - before["fade_out"]
        enters = after["dst_start"] + after["fade_in"]
        found.append(
            Transition(
                min(leaves, after["dst_start"]),
                max(_end_of(before), enters),
            )
        )
    return found


def review_start(transition: Transition) -> int:
    """The start of the window that reviews ``transition``: its fade start less 15 s."""
    return max(transition.start - GUARD_FRAMES, 0)


def guard_zones(found: Sequence[Transition]) -> list[Window]:
    """Each transition plus and minus 15 s, sorted, joined where they touch or overlap."""
    zones: list[Window] = []
    for t in sorted(found):
        lo, hi = t.start - GUARD_FRAMES, t.end + GUARD_FRAMES
        if zones and lo <= zones[-1].end:
            zones[-1] = Window(zones[-1].start, max(hi, zones[-1].end))
        else:
            zones.append(Window(lo, hi))
    return zones


def _out_of_zones(end: int, zones: Sequence[Window]) -> int:
    """``end``, or the end of the zone it lies strictly inside (later, never earlier)."""
    for zone in zones:
        if zone.start < end < zone.end:
            return zone.end
    return end


def place_window(
    tracks: Sequence[Mapping[str, Any]], start: int, selected: Transition | None = None
) -> Window:
    """The window that starts at ``start`` (rules 1 to 4 above). ``ValueError`` when ``start`` is
    not inside the mix, or ``selected`` cannot be whole in a window from ``start`` (it starts before
    ``start``, or it and its 15 s after would be over 6 min)."""
    total = total_frames(tracks)
    if not 0 <= start < total:
        raise ValueError("start")
    end = start + NOMINAL_FRAMES
    if selected is not None:
        needed = selected.end + GUARD_FRAMES
        if selected.start < start or needed - start > MAX_FRAMES:
            raise ValueError("selected")
        end = max(end, needed)
    end = _out_of_zones(end, guard_zones(transitions(tracks)))
    end = min(end, start + MAX_FRAMES, total)
    return Window(start, end)


def next_window(tracks: Sequence[Mapping[str, Any]], previous: Window) -> Window | None:
    """The window that follows ``previous`` (it starts where that one ends); ``None`` at the end
    of the mix."""
    if previous.end >= total_frames(tracks):
        return None
    return place_window(tracks, previous.end)


def windows_from(tracks: Sequence[Mapping[str, Any]], start: int) -> Iterator[Window]:
    """The contiguous windows of a session that starts at ``start``, to the end of the mix."""
    window: Window | None = place_window(tracks, start)
    while window is not None:
        yield window
        window = next_window(tracks, window)
