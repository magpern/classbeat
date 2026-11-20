"""What a cue may say and where it may sit on a track that is not ``exact`` (ADR 0040 section 3,
plan section 2). Pure: no I/O.

The design validator (``timing_gate``) refuses, on any track whose timing class is not ``exact``:

- **a beat-, bar- or drop-precise cue** (``precise_kind``): a countdown (``3, 2, 1``, "in 5
  seconds", "count down"), a beat or bar count ("4 bars", "8 counts", "on the downbeat", "next
  bar") or a drop hit ("on the drop", "when it drops"), in English, Swedish or Danish, the
  languages classbeat writes in. It is a pattern list over free text, so it is the second line:
  the first is that the model is told each track's class (``timing_input``);
- **a cue away from the track's anchors** (``track_timing_calc.anchors``): on an ``approximate``
  track a cue sits within ``CUE_MARGIN_S`` of a section start or end or of a drop or breakdown;
  on an ``unusable`` track within that margin of a section start or end only (its analysis is
  discarded, so none of its events is an anchor).

An ``exact`` track is never checked here, so a class whose tracks are all ``exact`` (every class
before SD-P6) is accepted exactly as before. Each problem names the interval and the track, and
says how to fix it (reword the cue, or leave it empty), so the correction round can fix it.
"""

import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Final, Literal

from mowl.workout import mmss

from classbeat.spotdown.spotdown_map_policy import APPROXIMATE_WINDOW_S
from classbeat.spotdown.timing_inputs_model import TimingClass

POLICY_VERSION: Final = "1"
CUE_MARGIN_S: Final = APPROXIMATE_WINDOW_S  # the ±2 s window of an approximate track's events

PreciseKind = Literal["countdown", "beat_or_bar", "drop_hit"]
_NUMBER_RUN: Final = re.compile(r"\d{1,2}(?:(?:\s*[,.\-\u2013\u2014\u2026!:]+\s*|\s+)\d{1,2}){2,}")
_PATTERNS: Final[tuple[tuple[PreciseKind, re.Pattern[str]], ...]] = (
    (
        "countdown",
        re.compile(
            r"\bcount(?:ing)?[\s-]*down\b|\bcountdown\b"
            r"|\b(?:in|om)\s+\d+\s*(?:s|sec|secs|seconds?|sek|sekund(?:er)?)\b"
            r"|\bthree\W+two\W+one\b|\btre\W+(?:två|to)\W+(?:ett|en|et)\b"
            r"|\bnedräkning\b|\bräkna\s+ner\b|\bnedtælling\b|\btæl\s+ned\b"
        ),
    ),
    (
        "beat_or_bar",
        re.compile(
            r"\bdown[\s-]*beat\b|\bbeat\s+(?:one|1)\b|\bon\s+the\s+one\b"
            r"|\b(?:\d+|two|four|eight|sixteen)[\s-]*(?:bars?|beats?|counts?)\b"
            r"|\b(?:next|this)\s+bar\b"
            r"|\b\d+\s*(?:takter|takt|taktslag|slag)\b|\b(?:nästa|næste)\s+takt\b"
            r"|\bpå\s+(?:ettan|etteren|ét-slaget|första\s+slaget|første\s+slag)\b"
        ),
    ),
    (
        "drop_hit",
        re.compile(
            r"\b(?:on|at|for|hit|wait\s+for)\s+the\s+drop\b|\bdrop\s+in\s+\d"
            r"|\bdrop\s+(?:hits|lands|comes)\b|\bwhen\s+(?:it|the\s+\w+)\s+drops\b"
            r"|\bpå\s+(?:droppen|droppet)\b|\bnär\s+(?:droppen|det\s+droppar)\b"
            r"|\bnår\s+(?:droppet|det\s+dropper)\b"
        ),
    ),
)
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


def _countdown(text: str) -> bool:
    """A run of three or more numbers, each one less than the one before (``3, 2, 1``)."""
    for run in _NUMBER_RUN.finditer(text):
        numbers = [int(n) for n in re.findall(r"\d+", run.group())]
        if any(
            a - b == 1 and b - c == 1
            for a, b, c in zip(numbers, numbers[1:], numbers[2:], strict=False)
        ):
            return True
    return False


def precise_kind(text: str) -> PreciseKind | None:
    """The kind of beat-, bar- or drop-precise instruction ``text`` gives, or ``None``."""
    folded = text.casefold()
    if _countdown(folded):
        return "countdown"
    return next((kind for kind, pattern in _PATTERNS if pattern.search(folded)), None)


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
