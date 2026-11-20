"""The facts ``track_fit_policy.check`` tests, read from an ``analysis-v1`` document's trusted
features only (``analysis_features_calc``). Pure: no I/O.

A **low-energy start** is an opening (the first ``OPENING_S``) whose mean full-band energy is at
most ``LOW_START_RATIO`` of the whole track's; unknown without a trusted energy curve.
"""

from collections.abc import Mapping
from typing import Final

from classbeat.spotdown import analysis_features_calc as fc
from classbeat.spotdown.timing_class_policy import TimingDecision
from classbeat.spotdown.track_fit_policy import TrackFacts

OPENING_S: Final = 30.0
LOW_START_RATIO: Final = 0.8

Document = Mapping[str, object]


def track_facts(doc: Document, decision: TimingDecision) -> TrackFacts:
    """The facts ``track_fit_policy.check`` tests, from trusted features only."""
    curve, duration = fc.energy_curve(doc), fc.read_duration(doc)
    low_start: bool | None = None
    if curve is not None and duration is not None:
        opening, whole = (
            fc.span_mean(curve, 0.0, min(OPENING_S, duration)),
            fc.span_mean(curve, 0.0, duration),
        )
        if opening is not None and whole is not None and whole > 0:
            low_start = opening <= LOW_START_RATIO * whole
    kinds = frozenset(e.kind for e in fc.trusted_events(doc))
    return TrackFacts(decision.timing_class, fc.read_bpm(doc), duration, low_start, kinds)
