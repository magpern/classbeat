"""The loop joins of a mix spec (ADR 0036 amendment A4, #1342). Pure: no I/O.

A **loop join** is a change of piece between two pieces of one reference, a **loop piece** a piece
on either side of one and a **loop run** a maximal run of adjacent pieces of one reference
(``mix_repeat_calc``). A **non-adjacent** repeat is an ordinary piece (A3): nothing here touches it.
``mix_plan_policy.checked`` calls these on the **placed** cuts (selected and snapped, A7 steps 2
and 3) and the **stored** facts (``TrackFacts``: the stored timing class and its candidates, never
a model's word), each at its step of A7:

- step 6, ``join_refusal``: for each loop join in mix order, ``loop_needs_exact_timing`` (A4.1: the
  track's stored timing decision is not ``exact``; ``approximate``, ``unusable`` or none), then
  ``loop_not_on_phrase`` (A4.2): the outgoing piece's ``end_sec`` and the incoming piece's
  ``start_sec`` each within ``LOCK_TOLERANCE_MS`` of an **anchor**, a trusted candidate's own time
  (``anchors``: the trusted phrase starts and ends when the track has any, else its trusted
  downbeats; none: refused). Compared in whole milliseconds, the tolerance included. The snapped
  cut is on the ``TrackFacts`` bar grid and the anchor is spotdown's measured point, not re-snapped,
  so where the two drift apart late in a long track the join is refused (it fails closed);
- step 7, ``length_refusal``: each loop piece at least ``MIN_LOOP_BARS`` bars of the track's
  ``TrackFacts.bpm`` (``loop_piece_too_short``, A4.4), then every loop run, its windows together
  (a run of one piece is an ordinary piece), at least ``MIN_PLAYED_SEC`` (``window_too_short``);
- step 8, ``crossfade_refusal``: a loop join's crossfade is 0 (``loop_crossfade_not_zero``, A4.3),
  after the crossfade's own shape and range and before ``crossfade_too_long``.

Every detail names piece positions, never a reference.
"""

from collections.abc import Mapping, Sequence
from typing import Final

from classbeat.studio.mix_cut_policy import trusted
from classbeat.studio.mix_facts_model import TrackFacts, cs
from classbeat.studio.mix_repeat_calc import loop_joins, runs

NEEDS_EXACT: Final = "loop_needs_exact_timing"
NOT_ON_PHRASE: Final = "loop_not_on_phrase"
PIECE_TOO_SHORT: Final = "loop_piece_too_short"
CROSSFADE_NOT_ZERO: Final = "loop_crossfade_not_zero"
WINDOW_TOO_SHORT: Final = "window_too_short"
LOCK_TOLERANCE_MS: Final = 30  # the lock tolerance of #1330
MIN_LOOP_BARS: Final = 8  # one phrase of four-four
PHRASE_KINDS: Final = frozenset({"phrase_start", "phrase_end"})

Piece = tuple[str, float, float]  # (reference, start_sec, end_sec), placed
Refusal = tuple[str, str]


def anchors(facts: TrackFacts) -> tuple[float, ...]:
    """The times a loop join may cut at: the trusted phrase points, else the trusted downbeats."""
    found = [c for c in facts.candidates if trusted(c, facts)]
    phrases = tuple(c.at_s for c in found if c.kind in PHRASE_KINDS)
    return phrases or tuple(c.at_s for c in found if c.kind == "downbeat")


def _ms(sec: float) -> int:
    return round(float(sec) * 1000)


def on_anchor(at: float, facts: TrackFacts) -> bool:
    return any(abs(_ms(at) - _ms(a)) <= LOCK_TOLERANCE_MS for a in anchors(facts))


def join_refusal(pieces: Sequence[Piece], facts: Mapping[str, TrackFacts]) -> Refusal | None:
    """Step 6: the first loop join in mix order that is not exact or not on an anchor."""
    for n in loop_joins([r for r, _, _ in pieces]):
        known = facts[pieces[n][0]]
        if known.timing_class != "exact":
            return (
                NEEDS_EXACT,
                f"pieces {n} and {n + 1} are the same track back to back (a loop), which needs "
                "the track's exact timing; put another track between them",
            )
        if not on_anchor(pieces[n][2], known) or not on_anchor(pieces[n + 1][1], known):
            return (
                NOT_ON_PHRASE,
                f"the loop between pieces {n} and {n + 1} cuts within {LOCK_TOLERANCE_MS} ms of "
                "a trusted phrase point (a downbeat when the track has none) on both sides",
            )
    return None


def length_refusal(
    pieces: Sequence[Piece], facts: Mapping[str, TrackFacts], min_played_sec: float
) -> Refusal | None:
    """Step 7: the loop pieces' bars, then every loop run's played seconds."""
    references = [r for r, _, _ in pieces]
    windows = [cs(b) - cs(a) for _, a, b in pieces]
    looped = sorted({m for n in loop_joins(references) for m in (n, n + 1)})
    for n in looped:
        if windows[n] < cs(MIN_LOOP_BARS * 240.0 / facts[references[n]].bpm):
            return PIECE_TOO_SHORT, f"piece {n} of a loop plays at least {MIN_LOOP_BARS} bars"
    for first, last in runs(references):
        if sum(windows[first : last + 1]) < cs(min_played_sec):
            if first == last:
                return WINDOW_TOO_SHORT, f"at least {min_played_sec:.0f} s of each piece"
            return (
                WINDOW_TOO_SHORT,
                f"at least {min_played_sec:.0f} s of the loop of pieces {first} to {last}",
            )
    return None


def crossfade_refusal(references: Sequence[str], crossfades: Sequence[int]) -> Refusal | None:
    """Step 8, after each crossfade's shape and range: no crossfade at a loop join."""
    for n in loop_joins(references):
        if crossfades[n] != 0:
            return CROSSFADE_NOT_ZERO, f"the loop between pieces {n} and {n + 1} has crossfade 0"
    return None
