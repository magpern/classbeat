"""The timing class of one analysed track (ADR 0040 section 3, plan section 2). Pure: no I/O.

The backend decides the class, never the model, and the **strict ``exact`` rule** holds: a track is
``exact`` only when

- **(a) the analysed file is the played recording**: classbeat uploaded the bytes of the very item
  that is played (the published SwedeBeat reference, the user's own played file, or the played
  mix) and spotdown's ``provenance.audio.sha256`` equals the sha256 of those bytes. An equal
  decoded-audio fingerprint is **not** enough (an equivalence signal only, never an identity); or
- **(b) verified beat-level equivalence**: a beat-level reference of the played audio (its own beat
  grid or audio fingerprint, not structure or metadata) agrees with the analysed grid, after the
  offset and over the whole track, with a median error of at most ``EXACT_MEDIAN_MS`` and at least
  ``EXACT_WITHIN_FRACTION`` of the beats within ``EXACT_WINDOW_MS``. Only for audio classbeat
  holds: classbeat has no beat-level reference of Spotify's audio, so (b) for a Spotify track is
  a later enhancement with a new ``POLICY_VERSION`` (plan section 2, "Consequence").

**Every YouTube analysis is at best ``approximate``**, whatever the evidence: a confident match, an
equal ISRC, a similar duration and agreeing MOWL anchors are no proof of identical audio. The ISRC
is kept in the evidence only; nothing here reads it. MOWL anchors can only move a track down to
``unusable`` (``disagree``), never up.

``approximate`` needs a ``confident`` or ``uncertain`` recording match, both duration checks within
tolerance and an ``ok`` alignment with a residual of at most ``RESIDUAL_MAX_S``. Anything else is
``unusable``: not the same recording, a residual above 2 s, a failed or missing alignment. Every
threshold is phrased as the condition that must hold, so a NaN never passes one.
"""

import math
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Final

from classbeat.spotdown.timing_inputs_model import (
    HELD_KINDS,
    Analysed,
    Played,
    TimingClass,
    TimingInputs,
)

POLICY_VERSION: Final = "1"
EXACT_MEDIAN_MS: Final = 20.0
EXACT_WINDOW_MS: Final = 50.0  # the window of ``within_window_fraction``
EXACT_WITHIN_FRACTION: Final = 0.95
RESIDUAL_MAX_S: Final = 2.0

_MATCH_BANDS: Final = frozenset({"confident", "uncertain"})
_DURATION_OK: Final = frozenset({"ok", "tolerable"})
_BEAT_REFERENCES: Final = frozenset({"beat_grid", "audio_fingerprint"})


@dataclass(frozen=True, slots=True)
class TimingDecision:
    timing_class: TimingClass
    reason: str
    evidence: Mapping[str, object]
    policy_version: str = POLICY_VERSION


def is_played_recording(played: Played, analysed: Analysed) -> bool:
    """Path (a): classbeat's own bytes of the played item, the same sha256."""
    return (
        played.kind in HELD_KINDS
        and played.sha256 is not None
        and analysed.source_kind == "upload"
        and analysed.uploaded_as == (played.kind, played.ref)
        and analysed.sha256 == played.sha256
    )


def is_beat_equivalent(inputs: TimingInputs) -> bool:
    """Path (b): verified beat-level equivalence, never for a YouTube or Spotify track."""
    eq = inputs.equivalence
    if eq is None or inputs.analysed.source_kind == "youtube":
        return False
    return (
        inputs.played.kind in HELD_KINDS
        and eq.reference in _BEAT_REFERENCES
        and eq.whole_track is True
        and eq.median_error_ms <= EXACT_MEDIAN_MS
        and eq.within_window_fraction >= EXACT_WITHIN_FRACTION
    )


def _evidence(inputs: TimingInputs) -> dict[str, object]:
    played, analysed = inputs.played, inputs.analysed
    out: dict[str, object] = {
        "played_kind": played.kind,
        "source_kind": analysed.source_kind,
        "sha256_equal": played.sha256 is not None and analysed.sha256 == played.sha256,
        "uploaded_as_played": analysed.uploaded_as == (played.kind, played.ref),
        "anchors": inputs.anchors,
    }
    if (m := inputs.match) is not None:
        out |= {
            "match_band": m.band,
            "recording_match_confidence": m.confidence,
            "duration_check": m.duration_check,
            "decoded_duration_check": m.decoded_check,
            "isrc_equal": m.isrc_equal,
        }
    if (a := inputs.alignment) is not None:
        out |= {"alignment": a.result, "residual_s": a.residual_s, "offset_s": a.offset_s}
    if (eq := inputs.equivalence) is not None:
        out |= {
            "beat_reference": eq.reference,
            "beat_median_error_ms": eq.median_error_ms,
            "beats_within_window": eq.within_window_fraction,
            "beat_whole_track": eq.whole_track,
        }
    return out


def _not_exact(inputs: TimingInputs) -> tuple[TimingClass, str]:
    m, a = inputs.match, inputs.alignment
    if a is not None and a.result == "failed":
        return "unusable", "alignment_failed"
    if m is None:
        return "unusable", "no_recording_match"
    if m.band not in _MATCH_BANDS or not 0.0 <= m.confidence <= 1.0:
        return "unusable", "not_same_recording"
    if m.duration_check not in _DURATION_OK or m.decoded_check not in _DURATION_OK | {None}:
        return "unusable", "duration_rejected"
    if a is None or a.result != "ok" or a.residual_s is None:
        return "unusable", "no_alignment"
    if not (math.isfinite(a.residual_s) and 0.0 <= a.residual_s <= RESIDUAL_MAX_S):
        return "unusable", "residual_too_large"
    if inputs.anchors == "disagree":
        return "unusable", "anchors_disagree"
    return "approximate", "matched_recording"


def decide(inputs: TimingInputs) -> TimingDecision:
    """The timing class, its reason and the evidence it rests on (stored with the class)."""
    evidence = _evidence(inputs)
    if is_played_recording(inputs.played, inputs.analysed):
        return TimingDecision("exact", "played_recording", evidence)
    if is_beat_equivalent(inputs):
        return TimingDecision("exact", "beat_equivalence", evidence)
    timing_class, reason = _not_exact(inputs)
    return TimingDecision(timing_class, reason, evidence)
