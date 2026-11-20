"""The shadow comparison of one studio track (ADR 0040 section 9, rollout step 1; plan section 7):
classbeat's own map of the track (``mowl.track.analyze_track``, today's baseline) against
spotdown's ``analysis-v1`` of the same bytes. Pure: no I/O.

**Only numbers, booleans and counts come out** (``Comparison.metrics``), under fixed names: never a
title, a reference, a source ref, a scope or anything else of text. The metrics are the gates'
(mir_eval-style, ADR 0040 section 9):

- **BPM**: both values, the relative error, within ``BPM_TOLERANCE`` (2 %) exactly and
  octave-aware (the local BPM times 1/2, 1 or 2);
- **beats**: the F-measure at ``BEAT_WINDOW_S`` (70 ms) of spotdown's trusted beats against the
  local grid (one tempo and beat 1 for the whole file: ``first_downbeat + k * 60 / bpm`` over
  ``[0, duration]``), the median signed offset of the matched beats, and both counts;
- **beat 1**: spotdown's first trusted downbeat minus the local one, in seconds;
- **boundaries**: the F-measure at 3 s (gated) and 1.5 s (reported) of spotdown's trusted segment
  boundaries against the local section starts, both inside the track (spotdown's segment that
  starts the music after a leading silence is not a boundary: the local map starts at 0), and
  both counts;
- **durations**: the local one, spotdown's signal and content durations, and the difference.

A match is one-to-one and greedy in time order, which is the maximum matching while the window is
under half the smallest gap between two points (beats at 400 BPM are 150 ms apart; sections are at
least 8 s long). An F-measure is ``None`` when either side has nothing to match.
"""

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from statistics import median
from typing import Final

from classbeat.spotdown import analysis_features_calc as fc
from classbeat.spotdown import feature_trust_policy as ft
from classbeat.spotdown.provenance_read_calc import audio_identity

BPM_TOLERANCE: Final = 0.02
OCTAVES: Final = (0.5, 1.0, 2.0)
BEAT_WINDOW_S: Final = 0.07
BOUNDARY_WINDOW_S: Final = 3.0
BOUNDARY_FINE_WINDOW_S: Final = 1.5
MAX_POINTS: Final = 100_000  # beats or boundaries on one side; more compares nothing
_DIGITS: Final = 4
_EDGE_S: Final = 0.001  # a segment that starts the music (after leading silence) is no change

Number = float | int | bool | None
Pair = tuple[float | None, float | None, int, int]


@dataclass(frozen=True, slots=True)
class Comparison:
    bpm_local: float | None
    bpm_spotdown: float | None
    beats: Pair  # F, median offset (ms), local, spotdown
    downbeat_delta_s: float | None
    boundaries: Pair  # F 3 s, F 1.5 s, local, spotdown
    duration_local_s: float | None
    duration_spotdown_s: float | None
    content_duration_s: float | None

    def metrics(self) -> dict[str, Number]:
        """The stored metrics: fixed names, numbers, booleans and ``None`` only."""
        error = _bpm_error(self.bpm_local, self.bpm_spotdown, (1.0,))
        octave = _bpm_error(self.bpm_local, self.bpm_spotdown, OCTAVES)
        beat_f, offset_ms, local_beats, spotdown_beats = self.beats
        bound_f, bound_fine_f, local_bounds, spotdown_bounds = self.boundaries
        delta = _difference(self.duration_spotdown_s, self.duration_local_s)
        return {
            "bpm_local": _round(self.bpm_local),
            "bpm_spotdown": _round(self.bpm_spotdown),
            "bpm_relative_error": _round(error),
            "bpm_agree": None if error is None else error <= BPM_TOLERANCE,
            "bpm_agree_octave": None if octave is None else octave <= BPM_TOLERANCE,
            "beat_f_70ms": _round(beat_f),
            "beat_offset_median_ms": _round(offset_ms),
            "beats_local": local_beats,
            "beats_spotdown": spotdown_beats,
            "downbeat_delta_s": _round(self.downbeat_delta_s),
            "boundary_f_3s": _round(bound_f),
            "boundary_f_1_5s": _round(bound_fine_f),
            "boundaries_local": local_bounds,
            "boundaries_spotdown": spotdown_bounds,
            "duration_local_s": _round(self.duration_local_s),
            "duration_spotdown_s": _round(self.duration_spotdown_s),
            "content_duration_s": _round(self.content_duration_s),
            "duration_delta_s": _round(delta),
        }


def _round(value: float | None) -> float | None:
    return None if value is None else round(value, _DIGITS)


def _difference(a: float | None, b: float | None) -> float | None:
    return None if a is None or b is None else a - b


def _bpm_error(local: float | None, other: float | None, factors: Sequence[float]) -> float | None:
    if local is None or other is None or local <= 0:
        return None
    return min(abs(other - local * f) / (local * f) for f in factors)


def matched_offsets(
    reference: Sequence[float], estimate: Sequence[float], window: float
) -> list[float]:
    """The signed offsets (estimate minus reference) of a one-to-one greedy match in time order;
    both sequences are sorted."""
    offsets: list[float] = []
    i = j = 0
    while i < len(reference) and j < len(estimate):
        gap = estimate[j] - reference[i]
        if abs(gap) <= window:
            offsets.append(gap)
            i, j = i + 1, j + 1
        elif gap < 0:
            j += 1
        else:
            i += 1
    return offsets


def f_measure(matched: int, reference: int, estimate: int) -> float | None:
    if reference == 0 or estimate == 0:
        return None
    if matched == 0:
        return 0.0
    precision, recall = matched / estimate, matched / reference
    return 2 * precision * recall / (precision + recall)


def _points(values: list[float] | None) -> list[float] | None:
    if values is None or len(values) > MAX_POINTS:
        return None
    return sorted(values)


def local_song(local: Mapping[str, object]) -> tuple[float | None, float | None, float | None]:
    """The local map's duration, BPM and beat 1 (``None`` where unreadable)."""
    duration = ft.num(local.get("duration"), 0.001, 24 * 3600)
    songs = ft.items(local.get("songs"))
    song = songs[0] if len(songs) == 1 else {}
    bpm = ft.num(song.get("bpm"), 1.0, 400.0)
    return duration, bpm, ft.num(song.get("first_downbeat"), 0.0)


def local_grid(duration: float | None, bpm: float | None, first: float | None) -> list[float]:
    """Beat times of one tempo through ``first`` over ``[0, duration]``."""
    if duration is None or bpm is None or first is None:
        return []
    period = 60.0 / bpm
    if duration / period > MAX_POINTS:
        return []
    start = first - math.floor(first / period) * period
    return [t for k in range(int(duration / period) + 2) if (t := start + k * period) <= duration]


def local_boundaries(local: Mapping[str, object], duration: float | None) -> list[float]:
    if duration is None:
        return []
    starts = (ft.num(s.get("start")) for s in ft.items(local.get("sections")))
    return sorted({s for s in starts if s is not None and 0 < s < duration})


def _trusted_times(doc: Mapping[str, object], name: str) -> list[float] | None:
    feature = ft.obj(doc, "grid", name)
    if feature is None or not ft.trusted(feature):
        return None
    return ft.numbers(feature.get("times_s"))


def _beats(grid: list[float], doc: Mapping[str, object]) -> Pair:
    spotdown = _points(_trusted_times(doc, "beats")) or []
    offsets = matched_offsets(grid, spotdown, BEAT_WINDOW_S)
    offset_ms = median(offsets) * 1000 if offsets else None
    return f_measure(len(offsets), len(grid), len(spotdown)), offset_ms, len(grid), len(spotdown)


def _boundaries(local: list[float], doc: Mapping[str, object]) -> Pair:
    duration = fc.read_duration(doc)
    spans = [] if duration is None else fc.section_spans(doc, duration)
    signal = ft.obj(doc, "signal")
    music = (None if signal is None else ft.num(signal.get("content_start_s"), 0.0)) or 0.0
    spotdown = _points([a for a, _ in spans[1:] if a > music + _EDGE_S]) or []
    coarse = matched_offsets(local, spotdown, BOUNDARY_WINDOW_S)
    fine = matched_offsets(local, spotdown, BOUNDARY_FINE_WINDOW_S)
    return (
        f_measure(len(coarse), len(local), len(spotdown)),
        f_measure(len(fine), len(local), len(spotdown)),
        len(local),
        len(spotdown),
    )


def compare(local: Mapping[str, object], doc: Mapping[str, object]) -> Comparison:
    """The comparison of the local map with spotdown's document of the same bytes."""
    duration, bpm, first = local_song(local)
    downbeats = _trusted_times(doc, "downbeats")
    identity = audio_identity(doc)
    return Comparison(
        bpm_local=bpm,
        bpm_spotdown=fc.read_bpm(doc),
        beats=_beats(local_grid(duration, bpm, first), doc),
        downbeat_delta_s=_difference(downbeats[0], first) if downbeats else None,
        boundaries=_boundaries(local_boundaries(local, duration), doc),
        duration_local_s=duration,
        duration_spotdown_s=fc.read_duration(doc),
        content_duration_s=None if identity is None else identity.content_duration_s,
    )
