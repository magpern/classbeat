"""A studio track's low-band energy curve (#1330): where the track has real bass. Pure: no I/O.

spotdown's ``analysis-v1`` has ``signal.energy.low_band`` (under 150 Hz, at a ``step_s`` of 1 s),
each value normalised to the track's own 95th percentile, so 1 is the track's usual bass and a
value near 0 a part with none (a breakdown, an intro without a kick). ``curve`` reads it only when
the feature is trusted (``feature_trust_policy``); the studio stores it in the track map under
``MAP_KEY`` (``studio_map_model``) and ``parse`` reads it back, whole or not at all.

``mean`` is the curve's mean over ``[start, end)`` of the track's own seconds (the values whose
time ``n * step`` lies there), ``None`` when no value does. The render's transition curve uses it
(``studio.mix_curve_policy``): a bass swap only where both sides of the overlap have real bass.
"""

import math
from collections.abc import Mapping
from typing import Final

from classbeat.spotdown import feature_trust_policy as ft

MAP_KEY: Final = "low_band"
VERSION: Final = "1"  # in the studio provenance's policy versions (``studio_map_model``)
MAX_VALUES: Final = 2 * 3600  # two hours at 1 s (the table's map check holds it)
Curve = tuple[float, tuple[float, ...]]  # (step_s, values)


def curve(doc: Mapping[str, object]) -> Curve | None:
    """The trusted low-band curve of an ``analysis-v1`` document, else ``None``."""
    low = ft.obj(doc, "signal", "energy", "low_band")
    if low is None or not ft.trusted(low):
        return None
    return parse({"step_s": low.get("step_s"), "values": low.get("values")})


def as_json(found: Curve) -> dict[str, object]:
    return {"step_s": found[0], "values": list(found[1])}


def parse(raw: object) -> Curve | None:
    """A stored curve read back; ``None`` for anything that is not one whole."""
    if not isinstance(raw, Mapping):
        return None
    step, values = raw.get("step_s"), raw.get("values")  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
    if isinstance(step, bool) or not isinstance(step, int | float) or not 0.01 <= step <= 60.0:
        return None
    if not isinstance(values, list) or not 0 < len(values) <= MAX_VALUES:  # pyright: ignore[reportUnknownArgumentType]
        return None
    out: list[float] = []
    for v in values:  # pyright: ignore[reportUnknownVariableType]
        if isinstance(v, bool) or not isinstance(v, int | float) or not math.isfinite(v):
            return None
        if not 0.0 <= v <= 1.0:
            return None
        out.append(float(v))
    return float(step), tuple(out)


def mean(found: Curve, start: float, end: float) -> float | None:
    step, values = found
    inside = [v for n, v in enumerate(values) if start <= n * step < end]
    return sum(inside) / len(inside) if inside else None
