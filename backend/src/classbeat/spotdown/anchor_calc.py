"""classbeat's MOWL anchor cross-check (ADR 0040 section 3, plan section 2). Pure: no I/O.

MOWL's sections for the same Spotify track are **structural** anchors. Each anchor start (on the
Spotify clock, after the first ``EDGE_S`` and before the last) is looked for among spotdown's
segment boundaries moved to the same clock (``reference_time = file_time + offset_s``). The share
found within ``WINDOW_S`` decides: ``agree`` at ``AGREE_MIN`` or more, ``disagree`` below
``DISAGREE_BELOW``, ``inconclusive`` between them or with fewer than ``MIN_ANCHORS``.

The result only feeds ``timing_class_policy``: it can move a track to ``unusable`` and never
makes one ``exact``.
"""

import math
from collections.abc import Iterable
from typing import Final

from classbeat.spotdown.timing_inputs_model import AnchorCheck

WINDOW_S: Final = 3.0  # the boundary tolerance of the quality gates (boundary F at +-3 s)
EDGE_S: Final = 1.0  # a section start at the very start or end says nothing about alignment
MIN_ANCHORS: Final = 3
AGREE_MIN: Final = 0.6
DISAGREE_BELOW: Final = 0.3


def _finite(values: Iterable[float]) -> list[float]:
    return [float(v) for v in values if isinstance(v, int | float) and math.isfinite(v)]


def agreement(
    anchors_s: Iterable[float],
    boundaries_s: Iterable[float],
    offset_s: float,
    reference_duration_s: float,
) -> float | None:
    """The share of usable anchors with a boundary within ``WINDOW_S``; ``None`` for too few."""
    if not (math.isfinite(offset_s) and math.isfinite(reference_duration_s)):
        return None
    moved = [b + offset_s for b in _finite(boundaries_s)]
    usable = [a for a in _finite(anchors_s) if EDGE_S < a < reference_duration_s - EDGE_S]
    if len(usable) < MIN_ANCHORS:
        return None
    found = sum(1 for a in usable if any(abs(a - b) <= WINDOW_S for b in moved))
    return found / len(usable)


def check(share: float | None) -> AnchorCheck:
    if share is None or not math.isfinite(share):
        return "inconclusive"
    if share >= AGREE_MIN:
        return "agree"
    return "disagree" if share < DISAGREE_BELOW else "inconclusive"
