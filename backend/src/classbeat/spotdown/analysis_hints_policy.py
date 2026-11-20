"""The hints of an ``analysis-v1`` document (ADR 0040 section 5): every ``heuristic`` or
low-confidence feature (labels, meter, phrases, events), for the model, marked as hints. Nothing
validates with them (``feature_trust_policy``). Pure: no I/O. Only the schema's own words reach a
hint (a label, an event kind, a method): anything else in the document reads as ``None``.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Final

from classbeat.spotdown import analysis_features_calc as fc
from classbeat.spotdown import feature_trust_policy as ft


@dataclass(frozen=True, slots=True)
class Hint:
    """A feature the model may see as a hint, never a check."""

    kind: str
    start_s: float | None
    value: str | None
    confidence: float | None
    method: str | None  # one of ``feature_trust_policy.METHODS``, else ``None``


_LABELS: Final = frozenset({"intro", "verse", "chorus", "bridge", "outro", "breakdown"})


def _method(feature: Mapping[str, object]) -> str | None:
    method = feature.get("method")
    return method if isinstance(method, str) and method in ft.METHODS else None


def _one_of(value: object, allowed: frozenset[str]) -> str | None:
    return value if isinstance(value, str) and value in allowed else None


def hints(doc: Mapping[str, object]) -> tuple[Hint, ...]:
    """Every heuristic or low-confidence feature, in document order."""
    out: list[Hint] = []
    for label in ft.items(doc.get("labels")):
        out.append(
            Hint(
                "label",
                None,
                _one_of(label.get("label"), _LABELS),
                ft.confidence(label),
                "heuristic",
            )
        )
    grid = ft.obj(doc, "grid") or {}
    meter = ft.obj(grid, "meter")
    if meter is not None and not ft.trusted(meter):
        beats = ft.num(meter.get("beats_per_bar"), 1, 32)
        value = None if beats is None else str(int(beats))
        out.append(Hint("meter", None, value, ft.confidence(meter), _method(meter)))
    for phrase in ft.items(grid.get("phrases")):
        if not ft.trusted(phrase):
            start = ft.num(phrase.get("start_s"), 0.0)
            out.append(Hint("phrase", start, None, ft.confidence(phrase), _method(phrase)))
    for event in ft.items(doc.get("events")):
        if not ft.trusted(event):
            start, kind = (
                ft.num(event.get("start_s"), 0.0),
                _one_of(event.get("kind"), fc.EVENT_KINDS),
            )
            out.append(Hint("event", start, kind, ft.confidence(event), _method(event)))
    return tuple(out)
