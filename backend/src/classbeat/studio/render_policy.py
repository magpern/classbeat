"""The pure rules of the mix render (ADR 0036 section 6, Mix): what a render plan may say and
when a rendered length is the composed length. Stdlib only and no I/O, because the render child
loads this file by path (it never imports the ``classbeat`` package).

A **render plan** is JSON: ``{"output": path, "tracks": [track, ...]}`` where a track is
``{"path", "src_start", "src_end", "dst_start", "fade_in", "fade_out"}``. Everything but the path
is a whole number of samples at 44100 Hz: the track plays its source samples
``[src_start, src_end)`` from output sample ``dst_start`` on, fading in over the first ``fade_in``
and out over the last ``fade_out`` samples (equal power, so an overlap of two matching fades keeps
the level). Tracks are in mix order: ``dst_start`` never decreases, and a track starts at most
30 s before the end of the tracks before it (an overlap) or after it (a gap of silence; likewise
the first track after the start): the render keeps only that much in memory. A track is never the
output file.
"""

import math
from typing import Any, Final

RATE: Final = 44100
TOLERANCE_SEC: Final = 0.1  # encoder padding, ADR 0036 section 6
MAX_TRACKS: Final = 200
MAX_SECONDS: Final = 9000  # 150 minutes (ADR 0036 section 4.2) of source or output
MAX_FRAMES: Final = MAX_SECONDS * RATE
MAX_SHIFT_FRAMES: Final = 30 * RATE  # the longest gap or overlap between tracks (memory is that)
MAX_MIX_BYTES: Final = 400 * 1000 * 1000  # the gateway's cap on the rendered file
TRACK_INTS: Final = ("src_start", "src_end", "dst_start", "fade_in", "fade_out")


def _whole(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= MAX_FRAMES


def validate_plan(plan: object) -> list[dict[str, Any]]:
    """The plan's tracks, or ``ValueError`` when the plan is not one this render accepts."""
    if not isinstance(plan, dict) or set(plan) != {"output", "tracks"}:  # pyright: ignore[reportUnknownArgumentType]
        raise ValueError("not a plan")
    tracks = plan["tracks"]  # pyright: ignore[reportUnknownVariableType]
    if not isinstance(plan["output"], str) or not plan["output"]:
        raise ValueError("no output")
    if not isinstance(tracks, list) or not 0 < len(tracks) <= MAX_TRACKS:  # pyright: ignore[reportUnknownArgumentType]
        raise ValueError("tracks")
    checked: list[dict[str, Any]] = []
    previous = ends = 0
    for track in tracks:  # pyright: ignore[reportUnknownVariableType]
        if not isinstance(track, dict) or set(track) != {"path", *TRACK_INTS}:  # pyright: ignore[reportUnknownArgumentType]
            raise ValueError("track")
        if not isinstance(track["path"], str) or not track["path"]:
            raise ValueError("path")
        if not all(_whole(track[k]) for k in TRACK_INTS):
            raise ValueError("samples")
        length = track["src_end"] - track["src_start"]
        if length <= 0 or track["fade_in"] + track["fade_out"] > length:
            raise ValueError("cut")
        if track["dst_start"] < previous:
            raise ValueError("order")
        if abs(track["dst_start"] - ends) > MAX_SHIFT_FRAMES:
            raise ValueError("gap or overlap")
        if track["path"] == plan["output"]:
            raise ValueError("path")
        previous = track["dst_start"]
        ends = max(ends, track["dst_start"] + length)
        checked.append(track)  # pyright: ignore[reportUnknownArgumentType]
    if composed_frames(checked) > MAX_FRAMES:
        raise ValueError("too long")
    return checked


def composed_frames(tracks: list[dict[str, Any]]) -> int:
    """The length of the composed mix in samples: where the last track ends."""
    return max(t["dst_start"] + t["src_end"] - t["src_start"] for t in tracks)


def composed_seconds(tracks: list[dict[str, Any]]) -> float:
    return composed_frames(tracks) / RATE


def length_ok(duration_sec: float, composed_sec: float) -> bool:
    """Whether the rendered file's decoded length is the composed one, within 100 ms (the
    encoder's padding). Never true for a length that is not a finite positive number."""
    if not all(math.isfinite(v) for v in (duration_sec, composed_sec)):
        return False
    if duration_sec <= 0 or composed_sec <= 0:
        return False
    return abs(duration_sec - composed_sec) <= TOLERANCE_SEC
