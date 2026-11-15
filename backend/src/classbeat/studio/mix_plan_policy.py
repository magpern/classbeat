"""The mix spec's rules (ADR 0036 section 4.2, SR C5). Pure: no I/O.

A mix spec is ``{"tracks": [{"reference", "start_sec", "end_sec"}], "crossfades": [int, ...]}``.
``checked`` validates one against the **stored analyses** of the approved list (``TrackFacts``:
each track's length, BPM and first downbeat from its analysis, never a model's number) and returns
it with its cuts snapped and rounded, or raises ``MixPlanRefused`` with one named reason:

- ``unknown_field`` / ``tempo_change``: a track names anything but its reference and cut (a tempo,
  rate, BPM, speed or pitch is a tempo change, which the gateway has no field for);
- ``list_has_gap``: the approved list has a gap (it can never go into a mix);
- ``not_a_permutation``: the order is not each approved reference exactly once;
- ``too_many_tracks``: more than ``MAX_TRACKS``;
- ``cut_out_of_range``: not ``0 <= start_sec < end_sec <= length``;
- ``window_too_short``: under ``MIN_PLAYED_SEC`` played once snapped;
- ``crossfade_count``: not one crossfade per change of track;
- ``crossfade_not_whole`` / ``crossfade_out_of_range``: not a whole number of seconds 0 to 30;
- ``crossfade_too_long``: not shorter than **both** neighbours' windows;
- ``crossfades_overlap``: a middle track's incoming plus outgoing crossfade is not shorter than
  its window (part of each track plays alone);
- ``mix_too_long``: the one duration rule (the windows less the crossfades) over ``MAX_TOTAL_SEC``.

**Snapping.** A cut moves to the nearest downbeat of the track's grid (its first downbeat plus
whole bars at its BPM). A start of 0 and an end at the track's length stay the file's own edges.
Cuts are stored rounded to 0.01 s, the app's precision and ``mowl.compose``'s arithmetic.
"""

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Final

MAX_TRACKS: Final = 40
MAX_TOTAL_SEC: Final = 150 * 60
MIN_PLAYED_SEC: Final = 60.0
MAX_CROSSFADE: Final = 30
EDGE: Final = 0.01  # the cut rounding; an end within it of the length is the file's end
TRACK_FIELDS: Final = frozenset({"reference", "start_sec", "end_sec"})
TEMPO_FIELDS: Final = frozenset({"tempo", "rate", "bpm", "speed", "pitch", "stretch"})


class MixPlanRefused(ValueError):
    def __init__(self, reason: str, detail: str) -> None:
        super().__init__(detail)
        self.reason = reason


@dataclass(frozen=True, slots=True)
class TrackFacts:
    """One approved track as its stored analysis has it."""

    length: float
    bpm: float
    first_downbeat: float


def _seconds(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float) or not math.isfinite(value):
        raise MixPlanRefused("cut_out_of_range", "a cut is a number of seconds")
    return float(value)


def _snap(at: float, facts: TrackFacts) -> float:
    if at <= 0:
        return 0.0
    if at >= facts.length - EDGE:
        return round(facts.length, 2)
    bar = 240.0 / facts.bpm
    nearest = facts.first_downbeat + round((at - facts.first_downbeat) / bar) * bar
    return round(min(max(nearest, 0.0), facts.length), 2)


def _track(raw: object, facts: Mapping[str, TrackFacts]) -> tuple[str, float, float]:
    if not isinstance(raw, Mapping):
        raise MixPlanRefused("unknown_field", "each track is an object")
    item: Mapping[str, Any] = raw  # pyright: ignore[reportUnknownVariableType]
    extra = set(item) - TRACK_FIELDS
    if extra & TEMPO_FIELDS:
        raise MixPlanRefused("tempo_change", "a mix plays every track at its own tempo")
    if extra:
        raise MixPlanRefused("unknown_field", "a track has its reference and its cut only")
    reference = item.get("reference")
    if not isinstance(reference, str) or reference not in facts:
        raise MixPlanRefused("not_a_permutation", "a track that is not on the approved list")
    known = facts[reference]
    start, end = _seconds(item.get("start_sec")), _seconds(item.get("end_sec"))
    if not 0 <= start < end <= known.length + EDGE:
        raise MixPlanRefused("cut_out_of_range", "need 0 <= start_sec < end_sec <= length")
    start, end = _snap(start, known), _snap(end, known)
    if end - start < MIN_PLAYED_SEC:
        raise MixPlanRefused("window_too_short", f"at least {MIN_PLAYED_SEC:.0f} s of each track")
    return reference, start, end


def _crossfades(raw: object, windows: Sequence[float]) -> list[int]:
    if not isinstance(raw, list | tuple) or len(raw) != len(windows) - 1:  # pyright: ignore[reportUnknownArgumentType]
        raise MixPlanRefused("crossfade_count", "one crossfade per change of track")
    out: list[int] = []
    for value in raw:  # pyright: ignore[reportUnknownVariableType]
        if isinstance(value, bool) or not isinstance(value, int):
            raise MixPlanRefused("crossfade_not_whole", "crossfades are whole seconds")
        if not 0 <= value <= MAX_CROSSFADE:
            raise MixPlanRefused("crossfade_out_of_range", f"0 to {MAX_CROSSFADE} s")
        out.append(value)
    for n, fade in enumerate(out):
        if fade >= min(windows[n], windows[n + 1]):
            raise MixPlanRefused("crossfade_too_long", "shorter than both neighbouring windows")
    for n in range(1, len(windows) - 1):
        if out[n - 1] + out[n] >= windows[n]:
            raise MixPlanRefused("crossfades_overlap", "part of each track must play alone")
    return out


def total_sec(windows: Sequence[float], crossfades: Sequence[int]) -> float:
    """The one duration rule: the played windows less the crossfades."""
    return round(sum(windows) - sum(crossfades), 2)


def checked(
    spec: Mapping[str, Any], approved: Sequence[str | None], facts: Mapping[str, TrackFacts]
) -> dict[str, Any]:
    """``spec`` validated against the approved order's references (``None`` for a gap) and their
    analyses, its cuts snapped; ``MixPlanRefused`` with the first rule it breaks."""
    if set(spec) - {"tracks", "crossfades"}:
        raise MixPlanRefused("unknown_field", "a mix spec has tracks and crossfades only")
    if any(r is None for r in approved):
        raise MixPlanRefused("list_has_gap", "a gap can never go into a mix")
    tracks = spec.get("tracks")
    if not isinstance(tracks, list | tuple) or not tracks:
        raise MixPlanRefused("not_a_permutation", "a mix has at least one track")
    if len(tracks) > MAX_TRACKS:  # pyright: ignore[reportUnknownArgumentType]
        raise MixPlanRefused("too_many_tracks", f"at most {MAX_TRACKS} tracks")
    known = {r: facts[r] for r in approved if r is not None and r in facts}
    cut = [_track(t, known) for t in tracks]  # pyright: ignore[reportUnknownVariableType]
    if sorted(r for r, _, _ in cut) != sorted(r for r in approved if r is not None):
        raise MixPlanRefused("not_a_permutation", "each approved track exactly once")
    windows = [end - start for _, start, end in cut]
    crossfades = _crossfades(spec.get("crossfades"), windows)
    if total_sec(windows, crossfades) > MAX_TOTAL_SEC:
        raise MixPlanRefused("mix_too_long", f"at most {MAX_TOTAL_SEC // 60} minutes")
    return {
        "tracks": [{"reference": r, "start_sec": a, "end_sec": b} for r, a, b in cut],
        "crossfades": crossfades,
    }
