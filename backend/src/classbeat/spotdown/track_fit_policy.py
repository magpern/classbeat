"""Suitability refinement of the selected tracks (ADR 0040 section 2, plan section 1 step 5). Pure.

``check`` tests one analysed track against the slot it fills in the class plan: its timing class
(``unusable`` never fits; a slot that needs ``exact`` timing needs an ``exact`` track), a usable
BPM (the BPM, half or double of it within the slot's range, ``BPM_TOLERANCE`` either side), a usable
length and the structure the slot needs (for example a warm-up's low-energy start). Facts come only
from trusted features (``feature_trust_policy``); an unknown fact the slot needs does not fit.

``plan_round`` bounds the refinement: at most ``MAX_ROUNDS`` rounds, and in each at most a third
of the list (rounded down) is replaced, the worst first, one track at a time (the spares first,
the caller's concern); a list under three tracks gets no round. An ``unusable`` track that has a
fallback (MOWL sections) falls back at once instead of using a replacement. After the last round
every track that still does not fit is accepted with a warning, or falls back when its timing is
``unusable``. **Falling back means today's path** for that track: its spotdown analysis is not
used (MOWL sections for a Spotify class; for a studio track without them, the local studio or mix
analysis, or the caller's replacement round). A ``Fit`` that ``check`` did not build (no reason,
an unknown one) is ranked worst, never an exception.
"""

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Final

from classbeat.spotdown.timing_inputs_model import TimingClass

POLICY_VERSION: Final = "1"
MAX_ROUNDS: Final = 2
BPM_TOLERANCE: Final = 0.03
STRUCTURES: Final = frozenset({"low_energy_start", "build_up", "drop", "breakdown"})
# Worst first: the order a round replaces in.
SEVERITY: Final = (
    "timing_unusable",
    "needs_exact_timing",
    "bpm_unknown",
    "bpm_unusable",
    "length_unknown",
    "too_short",
    "too_long",
    "structure_unknown",
    "structure_missing",
)


@dataclass(frozen=True, slots=True)
class TrackFacts:
    timing_class: TimingClass
    bpm: float | None
    duration_s: float | None
    low_energy_start: bool | None
    event_kinds: frozenset[str] = frozenset()


@dataclass(frozen=True, slots=True)
class SlotNeed:
    bpm_min: float | None = None
    bpm_max: float | None = None
    min_s: float = 0.0
    max_s: float | None = None
    needs_exact: bool = False
    structure: frozenset[str] = field(default_factory=frozenset[str])


@dataclass(frozen=True, slots=True)
class Fit:
    fits: bool
    reasons: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class Refinement:
    replace: tuple[int, ...]  # this round, worst first
    waiting: tuple[int, ...]  # over this round's cap: the next round
    fall_back: tuple[int, ...]
    accept_with_warning: tuple[int, ...]
    done: bool  # the last step: nothing is replaced any more


def _bpm_fits(bpm: float, need: SlotNeed) -> bool:
    low = -float("inf") if need.bpm_min is None else need.bpm_min * (1 - BPM_TOLERANCE)
    high = float("inf") if need.bpm_max is None else need.bpm_max * (1 + BPM_TOLERANCE)
    return any(low <= reading <= high for reading in (bpm, bpm / 2, bpm * 2))


def _structure(facts: TrackFacts, wanted: str) -> str | None:
    if wanted == "low_energy_start":
        if facts.low_energy_start is None:
            return "structure_unknown"
        return None if facts.low_energy_start else "structure_missing"
    return None if wanted in facts.event_kinds else "structure_missing"


def check(facts: TrackFacts, need: SlotNeed) -> Fit:
    reasons: list[str] = []
    if facts.timing_class == "unusable":
        reasons.append("timing_unusable")
    elif need.needs_exact and facts.timing_class != "exact":
        reasons.append("needs_exact_timing")
    if need.bpm_min is not None or need.bpm_max is not None:
        if facts.bpm is None or not facts.bpm > 0:
            reasons.append("bpm_unknown")
        elif not _bpm_fits(facts.bpm, need):
            reasons.append("bpm_unusable")
    if facts.duration_s is None or not facts.duration_s > 0:
        reasons.append("length_unknown")
    elif not facts.duration_s >= need.min_s:
        reasons.append("too_short")
    elif need.max_s is not None and not facts.duration_s <= need.max_s:
        reasons.append("too_long")
    for wanted in sorted(need.structure):
        if wanted not in STRUCTURES:
            raise ValueError("unknown structure need")
        problem = _structure(facts, wanted)
        if problem is not None and problem not in reasons:
            reasons.append(problem)
    reasons.sort(key=SEVERITY.index)
    return Fit(not reasons, tuple(reasons))


def round_cap(list_length: int) -> int:
    """At most a third of the list per round, rounded down."""
    return max(list_length, 0) // 3


def _worst(fit: Fit) -> int:
    """The rank of a fit's worst reason; a reason ``check`` never gives (or none) ranks first."""
    ranks = [SEVERITY.index(r) if r in SEVERITY else -1 for r in fit.reasons]
    return min(ranks, default=-1)


def plan_round(
    fits: Sequence[Fit], rounds_done: int, has_fallback: frozenset[int] = frozenset()
) -> Refinement:
    """What the next round does; ``fits[i]`` is the check of the list's track ``i``."""
    unfit = [i for i, fit in enumerate(fits) if not fit.fits]
    unusable = {i for i in unfit if "timing_unusable" in fits[i].reasons}
    fall_back = sorted(unusable & has_fallback)
    rest = sorted(
        (i for i in unfit if i not in fall_back),
        key=lambda i: (_worst(fits[i]), i),
    )
    cap = round_cap(len(fits))
    if rounds_done >= MAX_ROUNDS or cap == 0 or not rest:
        stays = [i for i in rest if i in unusable]
        warned = sorted(i for i in rest if i not in unusable)
        return Refinement((), (), tuple(sorted(fall_back + stays)), tuple(warned), True)
    return Refinement(tuple(rest[:cap]), tuple(rest[cap:]), tuple(fall_back), (), False)
