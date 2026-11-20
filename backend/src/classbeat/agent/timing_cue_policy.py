"""What a cue may say and where it may sit on a track that is not ``exact`` (ADR 0040 section 3,
plan section 2). Pure: no I/O.

The design validator (``timing_gate``) refuses, on any track whose timing class is not ``exact``:

- **a beat-, bar- or drop-precise cue** (``timing_cue_text_policy.precise_kind``): a countdown,
  a beat or bar count or a drop hit, in English, Swedish or Danish;
- **a cue away from the track's anchors** (``track_timing_calc.anchors``): on an ``approximate``
  track a cue sits within ``CUE_MARGIN_S`` of a section start or end or of a drop or breakdown;
  on an ``unusable`` track within that margin of a section start or end only (its analysis is
  discarded, so none of its events is an anchor).

An ``exact`` track is never checked here, so a class whose tracks are all ``exact`` (every class
before SD-P6) is accepted exactly as before. Each problem names the interval and the track, and
says how to fix it (reword the cue, or leave it empty), so the correction round can fix it.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Final

from mowl.workout import mmss

from classbeat.agent.timing_cue_text_policy import PreciseKind, precise_kind
from classbeat.spotdown.spotdown_map_policy import APPROXIMATE_WINDOW_S
from classbeat.spotdown.timing_inputs_model import TimingClass

POLICY_VERSION: Final = "1"
CUE_MARGIN_S: Final = APPROXIMATE_WINDOW_S  # the ±2 s window of an approximate track's events

_LABELS: Final[dict[PreciseKind, str]] = {
    "countdown": "a countdown",
    "beat_or_bar": "a beat or bar count",
    "drop_hit": "a drop hit",
}


@dataclass(frozen=True, slots=True)
class CueAt:
    """One interval's cue where the fitted class starts it, on one non-``exact`` track."""

    number: int  # the interval's place in the class, from 1
    start_s: float
    track: int  # 0-based, as the model's input names tracks
    timing_class: TimingClass
    anchors: tuple[float, ...]
    text: str


def _near_anchor(cue: CueAt) -> bool:
    return any(abs(cue.start_s - a) <= CUE_MARGIN_S for a in cue.anchors)


def problems(cues: Sequence[CueAt]) -> list[str]:
    """What the validator refuses in ``cues``; empty when every cue is allowed."""
    out: list[str] = []
    for cue in cues:
        if cue.timing_class == "exact" or not cue.text:
            continue
        where = f"interval {cue.number} (from {mmss(cue.start_s)}) on track {cue.track}"
        kind = precise_kind(cue.text)
        if kind is not None:
            out.append(
                f"{where}: its cue gives {_LABELS[kind]}, but the track's timing is "
                f"{cue.timing_class}: only an exact track takes countdowns, beat or bar counts "
                "and drop hits. Word the cue by section and energy, or leave it empty."
            )
        elif not _near_anchor(cue):
            anchors = (
                "a section start or end, a drop or a breakdown"
                if cue.timing_class == "approximate"
                else "a section start or end"
            )
            out.append(
                f"{where}: the track's timing is {cue.timing_class}, so a cue sits within "
                f"{CUE_MARGIN_S:.0f} s of {anchors} of that track. Move the cue to the interval "
                "that starts there, or leave it empty."
            )
    return out
