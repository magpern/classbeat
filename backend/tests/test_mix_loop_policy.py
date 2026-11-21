"""#1342 part 2 (ADR 0036 amendment A4, A6, A7): the loop joins of a mix spec. The ``exact`` gate on
the stored timing class, the 30 ms phrase tolerance against the trusted candidates' own times,
the downbeat fallback, the loop piece's 8 bars and the run's 60 s, the crossfade 0 at a join, their
places in A7's fixed order, a long-track loop, and the duration rule with loops. Pure: no I/O."""

from dataclasses import replace
from typing import Any

import pytest
from mowl import compose

from classbeat.spotdown.cut_candidates_model import Candidate
from classbeat.studio import mix_loop_policy
from classbeat.studio.mix_plan_policy import MixPlanRefused, TrackFacts, checked, cs, total_sec

PLAIN = TrackFacts(length=240.0, bpm=120.0, first_downbeat=0.5)  # 2 s bars on 0.5 + 2n


def point(at: float, kind: str = "phrase_start", trusted: bool = True) -> Candidate:
    return Candidate(at, kind, 0.8, "estimated", trusted)


# Track a: exact, 480 s, phrases every 8 bars (16 s) from 0.5, so 0.5, 16.5, 32.5, ... 464.5.
PHRASES = tuple(point(0.5 + 16 * k) for k in range(30))
LOOPED = TrackFacts(480.0, 120.0, 0.5, "exact", PHRASES)
ORDER = ["a", "b", "c"]


def facts(a: TrackFacts = LOOPED, **more: TrackFacts) -> dict[str, TrackFacts]:
    return {"a": a, "b": PLAIN, "c": PLAIN, **more}


def spec(*cuts: tuple[str, Any, Any], crossfades: list[Any] | None = None) -> dict[str, Any]:
    """``cuts`` in mix order; the default crossfade is 0 at a loop join and 8 s elsewhere."""
    tracks = [{"reference": r, "start_sec": s, "end_sec": e} for r, s, e in cuts]
    if crossfades is None:
        crossfades = [0 if cuts[n][0] == cuts[n + 1][0] else 8 for n in range(len(cuts) - 1)]
    return {"tracks": tracks, "crossfades": crossfades}


def loop(*cuts: tuple[Any, Any], crossfades: list[Any] | None = None) -> dict[str, Any]:
    """A loop run of track a (``cuts``), then b and c whole."""
    return spec(*[("a", s, e) for s, e in cuts], ("b", 0, 240.0), ("c", 0, 240.0),
                crossfades=crossfades)  # fmt: skip


def reason(value: dict[str, Any], known: dict[str, TrackFacts] | None = None) -> str:
    order = list(known or facts())
    with pytest.raises(MixPlanRefused) as caught:
        checked(value, order, known or facts())
    return caught.value.reason


CHORUS = (32.5, 48.5)  # 8 bars, from one phrase point to the next


# --- accepted loops -------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "value",
    [
        loop((0.5, 64.5), (64.5, 128.5)),  # two halves, joined on the phrase point 64.5
        loop(CHORUS, CHORUS, CHORUS, CHORUS),  # one chorus four times in a row: 64 s
        loop((0, 64.5), (64.5, 240.3)),  # the run's outer edges are ordinary (0, off-phrase)
        loop((0.5, 32.5), (32.5, 60.5)),  # a run of exactly 60 s
        spec(("b", 0, 240.0), ("a", *CHORUS), ("a", *CHORUS), ("a", 32.5, 90.0), ("c", 0, 240.0),
             crossfades=[8, 0, 0, 8]),
    ],
)  # fmt: skip
def test_a_loop_on_phrase_points_of_an_exact_track_is_accepted(value: dict[str, Any]) -> None:
    good = checked(value, ORDER, facts())
    assert checked(good, ORDER, facts()) == good  # a checked spec checks unchanged (draft.py)


# --- every reason, table-driven -------------------------------------------------------------------

UNTRUSTED = tuple(replace(c, trusted=False) for c in PHRASES)
BEATS = tuple(point(0.5 + 2 * n, "downbeat") for n in range(240))


@pytest.mark.parametrize(
    ("value", "known", "expected"),
    [
        # A4.1, the exact gate: on the stored timing class, whatever the candidates say.
        (loop((0.5, 64.5), (64.5, 128.5)), facts(replace(LOOPED, timing_class="approximate")),
         "loop_needs_exact_timing"),
        (loop((0.5, 64.5), (64.5, 128.5)), facts(replace(LOOPED, timing_class="unusable")),
         "loop_needs_exact_timing"),
        (loop((0.5, 64.5), (64.5, 128.5)), facts(replace(LOOPED, timing_class=None)),
         "loop_needs_exact_timing"),
        # A4.2: off a phrase point on either side (66.5 is a bar, not a phrase point).
        (loop((0.5, 66.5), (66.5, 128.5)), None, "loop_not_on_phrase"),
        (loop((0.5, 64.5), (66.5, 128.5)), None, "loop_not_on_phrase"),
        (loop((0.5, 66.5), (64.5, 128.5)), None, "loop_not_on_phrase"),
        # an exact track with no trusted anchor at all fails closed
        (loop((0.5, 64.5), (64.5, 128.5)), facts(replace(LOOPED, candidates=())),
         "loop_not_on_phrase"),
        (loop((0.5, 64.5), (64.5, 128.5)), facts(replace(LOOPED, candidates=UNTRUSTED)),
         "loop_not_on_phrase"),
        # a trusted phrase grid wins: its downbeats are not anchors then
        (loop((0.5, 66.5), (66.5, 128.5)), facts(replace(LOOPED, candidates=PHRASES + BEATS)),
         "loop_not_on_phrase"),
        # A4.4: a loop piece of 7 bars (on two anchors 14 s apart) and of 4 bars
        (loop((0.5, 32.5), (32.5, 46.5), (32.5, 64.5)),
         facts(replace(LOOPED, candidates=(*PHRASES, point(46.5, "phrase_end")))),
         "loop_piece_too_short"),
        (loop((0.5, 32.5), (32.5, 40.5)),
         facts(replace(LOOPED, candidates=(*PHRASES, point(40.5, "phrase_end")))),
         "loop_piece_too_short"),
        # A4.4: the run of loop pieces under 60 s together (each piece 8 bars)
        (loop(CHORUS, CHORUS, CHORUS), None, "window_too_short"),
        (loop((0.5, 32.5), (32.5, 58.5)), None, "window_too_short"),
        # A4.3: a crossfade at the join
        (loop((0.5, 64.5), (64.5, 128.5), crossfades=[1, 8, 8]), None, "loop_crossfade_not_zero"),
        (loop(CHORUS, CHORUS, CHORUS, CHORUS, crossfades=[0, 0, 4, 8, 8]), None,
         "loop_crossfade_not_zero"),
    ],
)  # fmt: skip
def test_each_loop_reason(
    value: dict[str, Any], known: dict[str, TrackFacts] | None, expected: str
) -> None:
    assert reason(value, known) == expected


def test_without_a_trusted_phrase_grid_the_trusted_downbeats_are_the_anchors() -> None:
    known = facts(replace(LOOPED, candidates=UNTRUSTED + BEATS))
    good = checked(loop((0.5, 66.5), (66.5, 128.5)), ORDER, known)  # 66.5: a trusted downbeat
    assert good["tracks"][0]["end_sec"] == 66.5
    beats_untrusted = tuple(replace(c, trusted=False) for c in BEATS)
    known = facts(replace(LOOPED, candidates=beats_untrusted))
    assert reason(loop((0.5, 66.5), (66.5, 128.5)), known) == "loop_not_on_phrase"


@pytest.mark.parametrize("timing", ["approximate", "unusable", None])
def test_a_non_adjacent_repeat_of_a_track_without_exact_timing_is_accepted(timing: Any) -> None:
    known = facts(replace(LOOPED, timing_class=timing))
    value = spec(("a", 0.5, 64.5), ("b", 0, 240.0), ("a", 64.5, 128.5), ("c", 0, 240.0))
    assert checked(value, ORDER, known)["tracks"][2]["reference"] == "a"


# --- the 30 ms tolerance (A4.2), compared on the snapped cut against the anchor's own time --------


@pytest.mark.parametrize(
    ("anchor", "accepted"),
    [(64.5, True), (64.53, True), (64.47, True), (64.531, False), (64.469, False), (64.6, False)],
)
def test_the_snapped_cut_is_within_30_ms_of_the_anchor(anchor: float, accepted: bool) -> None:
    # 64.5 selects the anchor (within the 0.9 s tolerance), which snaps back to the bar 64.5.
    others = tuple(c for c in PHRASES if c.at_s != 64.5)
    known = facts(replace(LOOPED, candidates=(*others, point(anchor))))
    value = loop((0.5, 64.5), (64.5, 128.5))
    if accepted:
        good = checked(value, ORDER, known)
        assert good["tracks"][0]["end_sec"] == good["tracks"][1]["start_sec"] == 64.5
    else:
        assert reason(value, known) == "loop_not_on_phrase"


def test_the_tolerance_is_30_ms() -> None:
    assert mix_loop_policy.LOCK_TOLERANCE_MS == 30
    assert mix_loop_policy.MIN_LOOP_BARS == 8


# --- the long-track loop (A4.2: it fails closed where the grids drift apart) ---------------------


def test_a_long_track_loop_late_in_the_track_stays_on_its_phrase_point() -> None:
    # 30 minutes at 126 BPM from 0.37 s, spotdown's phrase points on the same grid to the ms.
    bar = 240.0 / 126.0
    phrases = tuple(point(round(0.37 + 8 * k * bar, 3)) for k in range(118))
    a = TrackFacts(1800.0, 126.0, 0.37, "exact", phrases)
    late = [c.at_s for c in phrases[-9::4]]  # about 1661, 1722 and 1783 s: 4 phrases a piece
    value = loop((late[0], late[1]), (late[1], late[2]))
    good = checked(value, ORDER, facts(a))
    for side in (good["tracks"][0]["end_sec"], good["tracks"][1]["start_sec"]):
        assert abs(side - late[1]) <= 0.030  # the snap rounds to 0.01 s: at most 5 ms apart


def test_a_long_track_whose_measured_phrases_drift_from_the_grid_is_refused_late() -> None:
    # The stored BPM is 126.0, the music's tempo 126.004 (spotdown measures each phrase point):
    # the grids part by about 60 microseconds a bar, so 30 ms after about 500 bars (some 950 s).
    # Early the loop is accepted; late it is refused with loop_not_on_phrase (A4.2 fails closed).
    true_bar = 240.0 / 126.004
    phrases = tuple(point(round(0.37 + 8 * k * true_bar, 3)) for k in range(118))
    a = TrackFacts(1800.0, 126.0, 0.37, "exact", phrases)

    def at(k: int) -> dict[str, Any]:  # a loop joined at phrase k + 4 (its outer edges ordinary)
        p = [phrases[k + n].at_s for n in (0, 4, 8)]
        return loop((p[0], p[1]), (p[1], p[2]))

    checked(at(10), ORDER, facts(a))  # the join at about 213 s: 7 ms apart
    assert reason(at(100), facts(a)) == "loop_not_on_phrase"  # about 1585 s: 50 ms apart
    first = next(k for k in range(110) if _refused(at(k), a)) + 4
    # 30 ms, give or take the 5 ms of the 0.01 s rounding, is 413 to 579 bars: phrase 52 to 72.
    assert 52 <= first <= 72


def _refused(value: dict[str, Any], a: TrackFacts) -> bool:
    try:
        checked(value, ORDER, facts(a))
    except MixPlanRefused as refused:
        if refused.reason != "loop_not_on_phrase":
            raise
        return True
    return False


# --- the fixed order (A7) with loops --------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "known", "expected"),
    [
        # step 5 before step 6: a missing track with a loop of an approximate track
        (spec(("a", 0.5, 64.5), ("a", 64.5, 128.5), ("b", 0, 240.0)),
         facts(replace(LOOPED, timing_class="approximate")), "not_a_permutation"),
        # step 6 before step 7: a short loop piece that is also off-phrase
        (loop((0.5, 34.5), (34.5, 44.5)), None, "loop_not_on_phrase"),
        # step 6, join by join in mix order: b's join (not exact) comes before a's (off-phrase)
        (spec(("b", 0, 120.5), ("b", 120.5, 240.0), ("a", 0.5, 66.5), ("a", 66.5, 128.5),
              ("c", 0, 240.0)), None, "loop_needs_exact_timing"),
        (spec(("a", 0.5, 66.5), ("a", 66.5, 128.5), ("b", 0, 120.5), ("b", 120.5, 240.0),
              ("c", 0, 240.0)), None, "loop_not_on_phrase"),
        # step 7, loop pieces' bars before the run's 60 s, and before any crossfade rule
        (loop((0.5, 32.5), (32.5, 40.5), crossfades=[31, 8, 8]),
         facts(replace(LOOPED, candidates=(*PHRASES, point(40.5, "phrase_end")))),
         "loop_piece_too_short"),
        (loop(CHORUS, CHORUS, CHORUS, crossfades=[5]), None, "window_too_short"),
        # step 8: count, whole and range come before the join's 0
        (loop((0.5, 64.5), (64.5, 128.5), crossfades=[4, 8]), None, "crossfade_count"),
        (loop((0.5, 64.5), (64.5, 128.5), crossfades=[4, 8, 2.5]), None, "crossfade_not_whole"),
        (loop((0.5, 64.5), (64.5, 128.5), crossfades=[4, 8, 31]), None, "crossfade_out_of_range"),
        # ... and the join's 0 before crossfade_too_long (20 s into a 16 s loop piece)
        (spec(("b", 0, 240.0), *[("a", *CHORUS)] * 4, ("c", 0, 240.0),
              crossfades=[20, 3, 0, 0, 8]), None, "loop_crossfade_not_zero"),
        (spec(("b", 0, 240.0), *[("a", *CHORUS)] * 4, ("c", 0, 240.0),
              crossfades=[20, 0, 0, 0, 8]), None, "crossfade_too_long"),
    ],
)  # fmt: skip
def test_the_loop_reasons_take_their_place_in_the_fixed_order(
    value: dict[str, Any], known: dict[str, TrackFacts] | None, expected: str
) -> None:
    assert reason(value, known) == expected


def test_the_details_name_pieces_never_a_reference() -> None:
    known = {"ref-AAAA1111": LOOPED, "ref-BBBB2222": PLAIN}
    for cuts, crossfades in [
        (((0.5, 66.5), (66.5, 128.5)), [0, 8]),
        (((0.5, 64.5), (64.5, 128.5)), [3, 8]),
        ((CHORUS, CHORUS, CHORUS), [0, 0, 8]),
    ]:
        value = {
            "tracks": [{"reference": "ref-AAAA1111", "start_sec": s, "end_sec": e} for s, e in cuts]
            + [{"reference": "ref-BBBB2222", "start_sec": 0, "end_sec": 240.0}],
            "crossfades": crossfades,
        }
        with pytest.raises(MixPlanRefused) as caught:
            checked(value, list(known), known)
        assert "ref-" not in str(caught.value)
        assert "pieces 0" in str(caught.value)


# --- the duration rule with loops (A6) ------------------------------------------------------------


def test_the_duration_counts_every_loop_piece_with_0_at_each_join() -> None:
    good = checked(loop(CHORUS, CHORUS, CHORUS, CHORUS), ORDER, facts())
    windows = [t["end_sec"] - t["start_sec"] for t in good["tracks"]]
    expected = 4 * 16.0 + 2 * 240.0 - 2 * 8
    assert total_sec(windows, good["crossfades"]) == expected
    maps = [
        {"duration": facts()[t["reference"]].length,
         "songs": [{"start": 0, "end": facts()[t["reference"]].length, "bpm": 120.0,
                    "first_downbeat": 0.5}], "events": []}
        for t in good["tracks"]
    ]  # fmt: skip
    assert compose.compose_map(maps, good)["duration"] == expected
    durations = [facts()[t["reference"]].length for t in good["tracks"]]
    offsets = compose.mix_offsets(good, 44100, durations)
    assert len(offsets) == 6  # one render entry per piece
    assert [o["dst_start"] for o in offsets[:4]] == [round(16.0 * n * 44100) for n in range(4)]


def test_an_eight_bar_piece_rounded_one_hundredth_short_is_refused_as_the_adr_states() -> None:
    # A4.4 compares ``cs(end) - cs(start) >= cs(8 * 240 / bpm)``. Each cut is rounded to 0.01 s
    # on its own, so a piece of exactly 8 bars on the grid can play 1 cs less than 8 bars: at
    # 90.7 BPM from 0.37 s, bars 1 to 9 are 3.02 to 24.18 s, 2116 cs against 2117, while bars 2
    # to 10 play 2117 cs. The rule fails closed here; a slack of the cut rounding would be the
    # Architect's call (ADR text), so this pins today's behaviour.
    bar = 240.0 / 90.7

    def three_times(k: int) -> tuple[dict[str, Any], dict[str, TrackFacts]]:
        at = [round(0.37 + n * bar, 3) for n in (k, k + 8)]
        a = TrackFacts(600.0, 90.7, 0.37, "exact", tuple(point(t) for t in at))
        return loop(*[(at[0], at[1])] * 3), facts(a)

    value, known = three_times(1)
    with pytest.raises(MixPlanRefused) as caught:
        checked(value, ORDER, known)
    assert caught.value.reason == "loop_piece_too_short"
    assert "piece 0 " in str(caught.value)
    value, known = three_times(2)
    good = checked(value, ORDER, known)
    assert cs(good["tracks"][0]["end_sec"]) - cs(good["tracks"][0]["start_sec"]) == 2117
