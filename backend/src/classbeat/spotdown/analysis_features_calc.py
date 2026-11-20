"""Trusted readings of an ``analysis-v1`` document (``feature_trust_policy``): BPM, duration, the
full-band energy curve, sections cut at trusted segment boundaries with their energy ranks, and
the trusted events. Pure: no I/O. Untrusted or malformed values read as absent.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from itertools import pairwise
from typing import Final

from classbeat.spotdown import feature_trust_policy as ft

ENERGY_SPREAD_MIN: Final = 0.05  # sections closer than this in mean energy sound alike
EVENT_KINDS: Final = frozenset(
    {"build_up", "drop", "breakdown", "hard_stop", "fade", "tempo_change"}
)

Document = Mapping[str, object]


def read_bpm(doc: Document) -> float | None:
    tempo = ft.obj(doc, "tempo")
    return ft.num(tempo.get("bpm"), 1.0, 400.0) if tempo and ft.trusted(tempo) else None


def read_duration(doc: Document) -> float | None:
    signal = ft.obj(doc, "signal")
    return None if signal is None else ft.num(signal.get("duration_s"), 0.001, 24 * 3600)


def energy_curve(doc: Document) -> tuple[float, list[float]] | None:
    full = ft.obj(doc, "signal", "energy", "full")
    if not ft.trusted(full) or full is None:
        return None
    step, values = ft.num(full.get("step_s"), 0.01, 60.0), ft.numbers(full.get("values"))
    return None if step is None or not values else (step, values)


def span_mean(curve: tuple[float, list[float]], start: float, end: float) -> float | None:
    step, values = curve
    inside = [v for n, v in enumerate(values) if start <= n * step < end]
    return ft.mean(inside)


def section_spans(doc: Document, duration: float) -> list[tuple[float, float]]:
    """Sections cut at trusted segment boundaries, from 0 to ``duration``."""
    cuts = {0.0, duration}
    for segment in ft.items(_segments(doc)):
        start = ft.num(segment.get("start_s"))
        if (
            start is not None
            and 0 < start < duration
            and ft.trusted(segment, "boundary_confidence")
        ):
            cuts.add(start)
    ordered = sorted(cuts)
    return list(pairwise(ordered))


def _segments(doc: Document) -> object:
    structure = ft.obj(doc, "structure")
    return None if structure is None else structure.get("segments")


def energy_ranks(doc: Document, spans: list[tuple[float, float]]) -> list[float | None]:
    """Each section's rank in its track, 0 quietest to 1 loudest; ``None`` when they sound alike."""
    curve = energy_curve(doc)
    means = [None if curve is None else span_mean(curve, a, b) for a, b in spans]
    known = [m for m in means if m is not None]
    if len(known) < 2 or len(known) != len(means) or max(known) - min(known) < ENERGY_SPREAD_MIN:
        return [None] * len(spans)
    return [_rank(m, known) for m in known]


def _rank(value: float, known: list[float]) -> float:
    below, same = sum(k < value for k in known), known.count(value)
    return round((below + (same - 1) / 2) / (len(known) - 1), 2)


@dataclass(frozen=True, slots=True)
class Event:
    t: float
    kind: str
    strength: float


def trusted_events(doc: Document) -> list[Event]:
    """The trusted events of the schema's kinds, in time order."""
    out: list[Event] = []
    for event in ft.items(doc.get("events")):
        t, kind = ft.num(event.get("start_s"), 0.0), event.get("kind")
        if (
            t is None
            or not isinstance(kind, str)
            or kind not in EVENT_KINDS
            or not ft.trusted(event)
        ):
            continue
        out.append(Event(t, kind, ft.num(event.get("strength"), 0.0, 1.0) or 0.0))
    return sorted(out, key=lambda e: e.t)
