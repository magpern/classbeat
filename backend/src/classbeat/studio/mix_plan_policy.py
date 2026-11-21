"""The mix spec's rules (ADR 0036 section 4.2, SR C5). Pure: no I/O.

A mix spec is ``{"tracks": [{"reference", "start_sec", "end_sec"}], "crossfades": [int, ...]}``,
each entry of ``tracks`` a **piece**. A reference may have more than one piece (a repeat; ADR 0036
amendment "a song may appear more than once in a mix", #1342).
``checked`` validates one against the **stored analyses** of the approved list (``TrackFacts``:
each track's length, BPM and first downbeat from its analysis, never a model's number, and its
stored timing class and cut candidates, #1328) and returns it with its cuts placed (a candidate
selected, snapped) and rounded, or raises ``MixPlanRefused`` with one named reason:

- ``unknown_field`` / ``tempo_change``: a track names anything but its reference and cut (a tempo,
  rate, BPM, speed or pitch is a tempo change, which the gateway has no field for);
- ``list_has_gap``: the approved list has a gap (it can never go into a mix);
- ``not_a_permutation``: no piece, a piece whose reference is not on the approved list, or an
  approved reference with no piece (each approved track at least once, and only those);
- ``too_many_tracks``: more than ``MAX_TRACKS`` pieces;
- ``cut_out_of_range``: not ``0 <= start_sec < end_sec <= length``, before or after placing;
- ``loop_needs_exact_timing`` / ``loop_not_on_phrase``: a loop join (two pieces of one track back
  to back) on a track whose stored timing is not ``exact``, or off its trusted phrase points
  (``mix_loop_policy``, A4.1 and A4.2);
- ``loop_piece_too_short``: a loop piece under ``MIN_LOOP_BARS`` bars (A4.4);
- ``window_too_short``: a piece, or a loop run's pieces together, under ``MIN_PLAYED_SEC`` played
  once snapped;
- ``crossfade_count``: not one crossfade per change of piece;
- ``crossfade_not_whole`` / ``crossfade_out_of_range``: not a whole number of seconds 0 to 30;
- ``loop_crossfade_not_zero``: a crossfade at a loop join that is not 0 (A4.3);
- ``crossfade_too_long``: not shorter than **both** neighbours' windows;
- ``crossfades_overlap``: a middle track's incoming plus outgoing crossfade is not shorter than
  its window (part of each track plays alone);
- ``mix_too_long``: the one duration rule (the windows less the crossfades) over ``MAX_TOTAL_SEC``.

**The order of the checks** (A7, with #580's steps for #1328; the first rule broken is the reason):

1. the spec's shape: ``unknown_field``, ``list_has_gap``, at least one piece, ``too_many_tracks``;
2. **per piece, in mix order** (``_track``): its fields (``tempo_change``, ``unknown_field``), its
   reference on the approved list (``not_a_permutation``), then for each cut A7 steps 1 to 4: its
   range (``cut_out_of_range``), the candidate selection and the snap (``mix_cut_policy.placed``:
   a trusted candidate of an ``exact`` track within the tolerance, else the proposed cut; the bar
   grid for ``exact`` or no stored timing decision only), and its range again once placed
   (``cut_out_of_range``, a named detail). One piece's checks all run before the next piece's;
3. coverage (step 5): each approved reference has a piece (``not_a_permutation``);
4. the loop joins (step 6, ``mix_loop_policy.join_refusal``), each join in mix order:
   ``loop_needs_exact_timing``, then ``loop_not_on_phrase``, on the stored timing class and
   candidates that ``TrackFacts`` carries;
5. the lengths (step 7, ``mix_loop_policy.length_refusal``): each loop piece at least
   ``MIN_LOOP_BARS`` (``loop_piece_too_short``), then every other piece and every loop run at
   least ``MIN_PLAYED_SEC`` (``window_too_short``), after coverage, so a short window with a
   missing track ends ``not_a_permutation``;
6. the crossfades (step 8: count, whole, range, ``loop_crossfade_not_zero``, too long, overlap),
   then the total (step 9, ``mix_too_long``).

Every constraint after step 4 is checked on the placed cuts. The soft checks (A7 step 10) are
``mix_hint_policy.hints``, which callers run on the checked spec: they never refuse.

Cuts are stored rounded to 0.01 s, the app's precision, and every comparison is in whole
hundredths (``cs``, as ``mowl.compose`` computes): a 60.00 s window is 6000 cs whatever its start,
and a 60.005 s track plays 6000 cs, not 6001, so 30 + 30 s of crossfade over it is refused here as
it would be there. ``TrackFacts`` and ``cs`` live in ``mix_facts_model``.
"""

import math
from collections.abc import Mapping, Sequence
from typing import Any, Final

from classbeat.studio import mix_loop_policy
from classbeat.studio.mix_cut_policy import placed
from classbeat.studio.mix_facts_model import CS, TrackFacts, cs

MAX_TRACKS: Final = 40
MAX_TOTAL_SEC: Final = 150 * 60
MIN_PLAYED_SEC: Final = 60.0
MAX_CROSSFADE: Final = 30
TRACK_FIELDS: Final = frozenset({"reference", "start_sec", "end_sec"})
TEMPO_FIELDS: Final = frozenset({"tempo", "rate", "bpm", "speed", "pitch", "stretch"})
COVERAGE: Final = "each approved track at least once, and only those"


class MixPlanRefused(ValueError):
    def __init__(self, reason: str, detail: str) -> None:
        super().__init__(detail)
        self.reason = reason


def _seconds(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float) or not math.isfinite(value):
        raise MixPlanRefused("cut_out_of_range", "a cut is a number of seconds")
    return float(value)


def _track(raw: object, facts: Mapping[str, TrackFacts]) -> tuple[str, float, float]:
    if not isinstance(raw, Mapping):
        raise MixPlanRefused("unknown_field", "each track is an object")
    item: Mapping[str, Any] = raw  # pyright: ignore[reportUnknownVariableType]
    extra = set(item) - TRACK_FIELDS
    if extra & TEMPO_FIELDS:
        raise MixPlanRefused("tempo_change", "a mix plays every track at its own tempo")
    if extra:
        raise MixPlanRefused("unknown_field", "a track has its reference and its cut only")
    reference = item.get("reference")
    if not isinstance(reference, str) or reference not in facts:
        raise MixPlanRefused("not_a_permutation", COVERAGE)
    known = facts[reference]
    start, end = _seconds(item.get("start_sec")), _seconds(item.get("end_sec"))
    if not 0 <= cs(start) < cs(end) <= cs(known.length) + 1:  # the library's 0.01 s of slack
        raise MixPlanRefused("cut_out_of_range", "need 0 <= start_sec < end_sec <= length")
    start, end = placed(start, known), placed(end, known)  # A7 steps 2 and 3
    if not 0 <= cs(start) < cs(end) <= cs(known.length):  # A7 step 4: the range once placed
        raise MixPlanRefused(
            "cut_out_of_range", "the placed cut (candidate, snap) leaves no window"
        )
    return reference, start, end


def _crossfades(raw: object, windows: Sequence[int], references: Sequence[str] = ()) -> list[int]:
    """``windows`` in hundredths of a second, ``references`` the pieces' in mix order."""
    if not isinstance(raw, list | tuple) or len(raw) != len(windows) - 1:  # pyright: ignore[reportUnknownArgumentType]
        raise MixPlanRefused("crossfade_count", "one crossfade per change of piece")
    out: list[int] = []
    for value in raw:  # pyright: ignore[reportUnknownVariableType]
        if isinstance(value, bool) or not isinstance(value, int):
            raise MixPlanRefused("crossfade_not_whole", "crossfades are whole seconds")
        if not 0 <= value <= MAX_CROSSFADE:
            raise MixPlanRefused("crossfade_out_of_range", f"0 to {MAX_CROSSFADE} s")
        out.append(value)
    loop = mix_loop_policy.crossfade_refusal(references, out)
    if loop is not None:
        raise MixPlanRefused(*loop)
    for n, fade in enumerate(out):
        if fade * CS >= min(windows[n], windows[n + 1]):
            raise MixPlanRefused("crossfade_too_long", "shorter than both neighbouring windows")
    for n in range(1, len(windows) - 1):
        if (out[n - 1] + out[n]) * CS >= windows[n]:
            raise MixPlanRefused("crossfades_overlap", "part of each track must play alone")
    return out


def total_sec(windows: Sequence[float], crossfades: Sequence[int]) -> float:
    """The one duration rule: the played windows (seconds) less the crossfades, in hundredths."""
    return (sum(cs(w) for w in windows) - CS * sum(crossfades)) / CS


def checked(
    spec: Mapping[str, Any], approved: Sequence[str | None], facts: Mapping[str, TrackFacts]
) -> dict[str, Any]:
    """``spec`` validated against the approved order's references (``None`` for a gap) and their
    analyses, its cuts snapped; ``MixPlanRefused`` with the first rule it breaks (see the module
    docstring for the order)."""
    if set(spec) - {"tracks", "crossfades"}:
        raise MixPlanRefused("unknown_field", "a mix spec has tracks and crossfades only")
    if any(r is None for r in approved):
        raise MixPlanRefused("list_has_gap", "a gap can never go into a mix")
    tracks = spec.get("tracks")
    if not isinstance(tracks, list | tuple) or not tracks:
        raise MixPlanRefused("not_a_permutation", "a mix has at least one piece")
    if len(tracks) > MAX_TRACKS:  # pyright: ignore[reportUnknownArgumentType]
        raise MixPlanRefused("too_many_tracks", f"at most {MAX_TRACKS} pieces")
    known = {r: facts[r] for r in approved if r is not None and r in facts}
    cut = [_track(t, known) for t in tracks]  # pyright: ignore[reportUnknownVariableType]
    if {r for r in approved if r is not None} - {r for r, _, _ in cut}:  # "only those": _track
        raise MixPlanRefused("not_a_permutation", COVERAGE)
    joins = mix_loop_policy.join_refusal(cut, known)  # A7 step 6, on the placed cuts
    if joins is not None:
        raise MixPlanRefused(*joins)
    lengths = mix_loop_policy.length_refusal(cut, known, MIN_PLAYED_SEC)  # A7 step 7
    if lengths is not None:
        raise MixPlanRefused(*lengths)
    windows = [cs(end) - cs(start) for _, start, end in cut]
    crossfades = _crossfades(spec.get("crossfades"), windows, [r for r, _, _ in cut])
    if sum(windows) - CS * sum(crossfades) > MAX_TOTAL_SEC * CS:
        raise MixPlanRefused("mix_too_long", f"at most {MAX_TOTAL_SEC // 60} minutes")
    return {
        "tracks": [{"reference": r, "start_sec": a, "end_sec": b} for r, a, b in cut],
        "crossfades": crossfades,
    }
