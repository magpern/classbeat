"""Where one cut of a mix piece lands: steps 2 and 3 of the fixed order (ADR 0036 amendment A7,
#580's approved design, #1328). Pure: no I/O. ``mix_plan_policy.checked`` runs step 1 (the range
of the proposed cut) before ``placed`` and step 4 (the range again) after it.

**Step 2, candidate selection** (``selected``): the nearest **usable** candidate within the
tolerance, else the proposed cut. Usable means ``trusted`` (inherited from its feature and the
timing class, ``spotdown.cut_candidates_calc``) **and** the track's stored timing class is
``exact``, checked here again: an ``approximate`` candidate is advisory and never sets a cut, an
``unusable`` track has none. A downbeat is taken only when no other kind is in reach (the snap
finds the bar anyway). A cut at an edge (0, or within the cut rounding of the length) is the
file's edge and is never moved: the whole track, intros, outros and fades, stays by default. An
edge candidate is never selected either.

**At a transition** (#1330) the phrase point of that side comes first: the outgoing piece's end
takes a ``phrase_end``, the incoming piece's start a ``phrase_start``, before any other kind in
reach, and a downbeat still comes last. The trust gate is the same (``usable``), so an
``approximate`` or ``unusable`` track keeps today's transitions, and without a trusted phrase point
in reach the selection is today's. ``Side`` names the cut: ``"end"`` of a piece that another
follows, ``"start"`` of a piece that follows another, ``None`` for the mix's own first start and
last end.

The tolerance is ``SELECT_TOLERANCE_S``, at most ``SELECT_BAR_SHARE`` of a bar. Under half a bar,
a snapped cut checked again selects only a point that snaps back to the same bar, so ``checked``
of a checked spec changes nothing (``swedebeat_studio.draft`` relies on it).

**Step 3, the snap** (``snapped``): to the nearest downbeat of the ``TrackFacts`` grid (its first
downbeat plus whole bars at its BPM), only for an ``exact`` track; an ``approximate`` or
``unusable`` track keeps its cut, rounded, and is never snapped to a grid. A start of 0 stays the
file's start, and an end within 0.01 s of the length, or past it, is the length in whole
hundredths (``cs(length)``, as ``mowl.compose._clamp`` clamps a window), **after** the snap and
the rounding as well as before: a bar that lands within the cut rounding of the end is the end,
so placing a placed cut again never moves it. Cuts are rounded to 0.01 s.
"""

from typing import Final, Literal

from classbeat.spotdown.cut_candidates_model import Candidate
from classbeat.studio.mix_facts_model import CS, TrackFacts, cs

SELECT_TOLERANCE_S: Final = 1.0
SELECT_BAR_SHARE: Final = 0.45  # under half a bar (see the module docstring)
Side = Literal["start", "end"] | None
PHRASE_OF: Final = {"start": "phrase_start", "end": "phrase_end"}


def snaps(facts: TrackFacts) -> bool:
    return facts.timing_class == "exact"


def trusted(candidate: Candidate, facts: TrackFacts) -> bool:
    """The trust gate: the candidate's inherited trust, on an ``exact`` track only."""
    return candidate.trusted and facts.timing_class == "exact"


def usable(facts: TrackFacts) -> tuple[Candidate, ...]:
    """The candidates that may set a cut: trusted (``trusted``) and not at an edge."""
    return tuple(c for c in facts.candidates if trusted(c, facts) and _edge(c.at_s, facts) is None)


def tolerance(facts: TrackFacts) -> float:
    return min(SELECT_TOLERANCE_S, SELECT_BAR_SHARE * 240.0 / facts.bpm)


def _edge(at: float, facts: TrackFacts) -> float | None:
    if at <= 0:
        return 0.0
    if cs(at) >= cs(facts.length) - 1:  # within the cut rounding of the end: the file's end
        return cs(facts.length) / CS  # never round(length, 2)
    return None


def _rank(kind: str, side: Side) -> int:
    """0 for the side's phrase point at a transition, 2 for a downbeat, 1 for any other kind."""
    if side is not None and kind == PHRASE_OF[side]:
        return 0
    return 2 if kind == "downbeat" else 1


def selected(at: float, facts: TrackFacts, side: Side = None) -> float:
    """Step 2: the nearest usable candidate within the tolerance, else ``at``; at a transition
    (``side``) its phrase point first."""
    if _edge(at, facts) is not None:
        return at
    reach = tolerance(facts)
    near = [c for c in usable(facts) if abs(c.at_s - at) <= reach]
    if not near:
        return at
    return min(near, key=lambda c: (_rank(c.kind, side), abs(c.at_s - at), c.at_s)).at_s


def snapped(at: float, facts: TrackFacts) -> float:
    """Step 3: the bar-grid snap (``exact`` or no decision), else the cut rounded."""
    edge = _edge(at, facts)
    if edge is not None:
        return edge
    if snaps(facts):
        bar = 240.0 / facts.bpm
        at = facts.first_downbeat + round((at - facts.first_downbeat) / bar) * bar
    rounded = round(max(at, 0.0), 2)
    edge = _edge(rounded, facts)  # once rounded, the edges again (0, or the length and past it)
    return rounded if edge is None else edge


def placed(at: float, facts: TrackFacts, side: Side = None) -> float:
    """Steps 2 and 3 for one cut."""
    return snapped(selected(at, facts, side), facts)
