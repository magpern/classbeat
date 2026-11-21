"""#1330: phrase-aligned and downbeat-locked transitions in the mix spec. The transition cut points
(``mix_cut_policy``, a phrase point of that side first, ``exact`` tracks only), the soft checks
(``mix_transition_policy``: the downbeat lock within 30 ms with the crossfades that would lock, and
the song stage's tempo check on the mix order) and the regression cases: the one duration rule, the
crossfade rules and the 10 % class-length tolerance with snapped transitions. Pure: no I/O."""

import random
from dataclasses import replace
from typing import Any

import pytest
from mowl import compose

from classbeat.agent.songs import within_tolerance
from classbeat.spotdown.cut_candidates_model import Candidate
from classbeat.studio import mix_transition_policy as mt
from classbeat.studio.mix_plan_policy import MixPlanRefused, TrackFacts, checked, total_sec

ORDER = ["a", "b", "c"]


def point(at: float, kind: str = "segment_start", trusted: bool = True) -> Candidate:
    return Candidate(at, kind, 0.8, "estimated", trusted)


def grid(bpm: float, first: float, length: float, trusted: bool = True) -> tuple[Candidate, ...]:
    bar = 240.0 / bpm
    count = int((length - first) / bar) + 1
    return tuple(point(round(first + k * bar, 4), "downbeat", trusted) for k in range(count))


def track(
    bpm: float = 120.0,
    first: float = 0.5,
    length: float = 240.0,
    *extra: Candidate,
    timing: Any = "exact",
    trusted: bool = True,
) -> TrackFacts:
    beats = grid(bpm, first, length, trusted)
    return TrackFacts(length, bpm, first, timing, (*beats, *extra))


PLAIN = TrackFacts(length=240.0, bpm=120.0, first_downbeat=0.5)  # 2 s bars on 0.5 + 2n


def spec(*cuts: tuple[str, Any, Any], crossfades: list[int]) -> dict[str, Any]:
    return {
        "tracks": [{"reference": r, "start_sec": s, "end_sec": e} for r, s, e in cuts],
        "crossfades": crossfades,
    }


def cuts_of(good: dict[str, Any]) -> list[tuple[float, float]]:
    return [(t["start_sec"], t["end_sec"]) for t in good["tracks"]]


# -- the transition points (mix_cut_policy, A7 step 2) ------------------------------------------

# 201.4 reaches a phrase end at 202.2 (0.8 away, snaps to 202.5) and a segment start at 200.7
# (0.7 away, snaps to 200.5): today the nearer other kind wins.
END_POINTS = (point(202.2, "phrase_end"), point(200.7, "segment_start"))
START_POINTS = (point(31.6, "phrase_start"), point(30.1, "segment_start"))  # from 30.8


def test_an_outgoing_end_takes_its_phrase_end_at_a_transition_only() -> None:
    a = replace(PLAIN, timing_class="exact", candidates=END_POINTS)
    facts = {"a": a, "b": PLAIN, "c": PLAIN}
    good = checked(
        spec(("a", 0, 201.4), ("b", 0, 240.0), ("c", 0, 240.0), crossfades=[8, 8]), ORDER, facts
    )
    assert cuts_of(good)[0] == (0.0, 202.5)  # a transition: the phrase end
    last = checked(
        spec(("b", 0, 240.0), ("c", 0, 240.0), ("a", 0, 201.4), crossfades=[8, 8]), ORDER, facts
    )
    assert cuts_of(last)[2] == (0.0, 200.5)  # the mix's own end: today's selection


def test_an_incoming_start_takes_its_phrase_start_at_a_transition_only() -> None:
    a = replace(PLAIN, timing_class="exact", candidates=START_POINTS)
    facts = {"a": a, "b": PLAIN, "c": PLAIN}
    good = checked(
        spec(("b", 0, 240.0), ("a", 30.8, 240.0), ("c", 0, 240.0), crossfades=[8, 8]), ORDER, facts
    )
    assert cuts_of(good)[1] == (32.5, 240.0)  # 31.6 -> its bar
    first = checked(
        spec(("a", 30.8, 240.0), ("b", 0, 240.0), ("c", 0, 240.0), crossfades=[8, 8]), ORDER, facts
    )
    assert cuts_of(first)[0] == (30.5, 240.0)  # the mix's own start: 30.1 -> 30.5, as today


def test_the_other_sides_phrase_point_is_no_better_than_any_other_kind() -> None:
    a = replace(
        PLAIN,
        timing_class="exact",
        candidates=(point(202.2, "phrase_start"), point(200.7, "segment_start")),
    )
    good = checked(
        spec(("a", 0, 201.4), ("b", 0, 240.0), ("c", 0, 240.0), crossfades=[8, 8]),
        ORDER,
        {"a": a, "b": PLAIN, "c": PLAIN},
    )
    assert cuts_of(good)[0] == (0.0, 200.5)


@pytest.mark.parametrize(
    ("timing", "trusted", "end"),
    [
        ("approximate", True, 201.4),  # advisory: never moved, never snapped
        ("unusable", True, 201.4),
        (None, True, 200.5),  # no stored decision: no candidate, the bar snap as before
        ("exact", False, 200.5),  # hints never set a cut: the trusted segment start does
    ],
)
def test_the_snap_to_a_phrase_point_is_for_trusted_candidates_of_exact_tracks_only(
    timing: Any, trusted: bool, end: float
) -> None:
    points = tuple(replace(c, trusted=trusted) for c in END_POINTS)
    if not trusted:
        points = (*points, point(200.7, "segment_start"))  # the trusted other kind: today's pick
    a = replace(PLAIN, timing_class=timing, candidates=points)
    good = checked(
        spec(("a", 0, 201.4), ("b", 0, 240.0), ("c", 0, 240.0), crossfades=[8, 8]),
        ORDER,
        {"a": a, "b": PLAIN, "c": PLAIN},
    )
    assert cuts_of(good)[0][1] == end


@pytest.mark.parametrize("seed", range(20))
def test_checking_a_checked_spec_with_phrase_points_changes_nothing(seed: int) -> None:
    rng = random.Random(seed)  # noqa: S311 - a reproducible table, not a secret
    bpm, length = rng.uniform(70.0, 190.0), rng.uniform(150.0, 400.0)
    kinds = ["phrase_start", "phrase_end", "segment_start", "downbeat"]
    points = tuple(
        point(round(rng.uniform(0.0, length), 3), rng.choice(kinds), rng.random() < 0.8)
        for _ in range(rng.randint(0, 80))
    )
    facts = {
        "a": TrackFacts(length, bpm, rng.uniform(0.0, 2.0), "exact", points),
        "b": PLAIN,
        "c": PLAIN,
    }
    for _ in range(25):
        start = round(rng.uniform(0.0, length / 2), 2)
        end = round(rng.uniform(start + 61.0, length + 0.005), 2)
        value = spec(("b", 0, 240.0), ("a", start, end), ("c", 0, 240.0), crossfades=[8, 8])
        try:
            good = checked(value, ORDER, facts)
        except MixPlanRefused:
            continue
        assert checked(good, ORDER, facts) == good


# -- the downbeat lock (mix_transition_policy.locked) ------------------------------------------


def test_two_tracks_of_one_tempo_cut_on_downbeats_lock_with_whole_bars() -> None:
    a, b = track(), track(first=0.25)
    assert mt.locked(a, 200.5, b, 10.25, 8) is True  # 4 bars of 2 s
    assert mt.locked(a, 200.5, b, 10.25, 7) is False  # 3.5 bars
    assert mt.locked(a, 201.5, b, 10.25, 8) is False  # the outgoing end a beat off its bar


@pytest.mark.parametrize(("shift", "locks"), [(0.03, True), (-0.03, True), (0.04, False)])
def test_the_lock_tolerance_is_30_ms(shift: float, locks: bool) -> None:
    a, b = track(), track(first=0.25)
    assert mt.LOCK_TOLERANCE_S == 0.03
    assert mt.locked(a, 200.5, b, round(10.25 + shift, 2), 8) is locks


def test_half_or_double_time_needs_only_the_slower_tracks_downbeats() -> None:
    slow, fast = track(bpm=64.0, first=0.0), track(bpm=128.0, first=0.0)
    bar = 240.0 / 64.0  # 3.75 s; 8 such bars are 30 s
    assert mt.locked(slow, 40 * bar, fast, 0.0, 30) is True
    assert mt.locked(fast, 80 * 1.875, slow, 0.0, 30) is True
    assert mt.locked(fast, 80 * 1.875, fast, 0.0, 30) is True
    assert mt.locked(slow, 40 * bar, fast, 0.94, 30) is False  # half a fast bar off


@pytest.mark.parametrize(
    ("a", "b", "fade"),
    [
        (track(), track(), 0),  # no overlap: nothing to lock
        (track(timing="approximate"), track(), 8),
        (track(), track(timing="unusable"), 8),
        (track(), track(timing=None), 8),  # a local analysis has no trusted downbeats
        (track(trusted=False), track(), 8),  # the downbeats are hints
        (PLAIN, track(), 8),  # no candidates at all
    ],
)
def test_the_lock_applies_only_to_trusted_downbeats_of_exact_tracks(
    a: TrackFacts, b: TrackFacts, fade: int
) -> None:
    assert mt.locked(a, 201.4, b, 10.25, fade) is None


def test_the_crossfades_that_would_lock_are_whole_bars_nearest_first() -> None:
    a, b = track(), track(first=0.25)
    assert mt.locking_crossfades(a, 200.5, b, 10.25, 7, 30) == (6, 8)
    fast_a, fast_b = track(bpm=128.0), track(bpm=128.0, first=0.25)  # 1.875 s bars
    assert mt.locking_crossfades(fast_a, 0.5 + 100 * 1.875, fast_b, 0.25, 8, 30) == (15, 30)
    assert mt.locking_crossfades(fast_a, 0.5 + 100 * 1.875, fast_b, 0.25, 8, 29) == (15,)
    odd_a, odd_b = track(bpm=125.0), track(bpm=125.0, first=0.25)  # 1.92 s: none within 30 ms
    assert mt.locking_crossfades(odd_a, 0.5 + 100 * 1.92, odd_b, 0.25, 8, 30) == ()


# -- the hints (A7 step 10) --------------------------------------------------------------------


def hints_of(facts: dict[str, TrackFacts], *cuts: tuple[str, Any, Any], fades: list[int]) -> Any:
    good = checked(spec(*cuts, crossfades=fades), [c[0] for c in cuts], facts)
    return good, mt.hints(good, facts)


def test_a_lock_that_is_not_met_is_a_named_hint_with_the_crossfades_that_would() -> None:
    facts = {"a": track(), "b": track(first=0.25), "c": track(bpm=125.0)}
    good, found = hints_of(
        facts, ("a", 0, 200.5), ("b", 10.25, 240.0), ("c", 0, 240.0), fades=[7, 8]
    )
    assert good["crossfades"] == [7, 8]  # never changed by a hint
    assert [(h.code, h.pieces) for h in found] == [
        ("downbeats_not_locked", (0, 1)),
        ("downbeats_not_locked", (1, 2)),
    ]
    assert found[0].crossfades == (6, 8)
    text = found[0].text()
    assert text.startswith("downbeats_not_locked: in the crossfade between pieces 0 and 1")
    assert "6 s or 8 s (whole bars)" in text
    assert found[1].crossfades == ()  # 120 against 125 BPM: no whole second locks them
    assert "No crossfade from 1 to 30 s locks them with these cuts" in found[1].text()


def test_a_locked_mix_and_one_without_trusted_downbeats_have_no_lock_hint() -> None:
    facts = {"a": track(), "b": track(first=0.25), "c": track(first=0.25)}
    _, found = hints_of(
        facts, ("a", 0, 200.5), ("b", 10.25, 200.25), ("c", 30.25, 240.0), fades=[8, 10]
    )
    assert found == []
    plain = {"a": PLAIN, "b": PLAIN, "c": PLAIN}
    _, found = hints_of(plain, ("a", 0, 201.4), ("b", 10.3, 240.0), ("c", 0, 240.0), fades=[7, 7])
    assert found == []  # today's transitions: nothing to check


def test_a_tempo_jump_is_flagged_except_half_and_double_time_and_never_changes_a_tempo() -> None:
    facts = {
        "a": replace(PLAIN, bpm=120.0),
        "b": replace(PLAIN, bpm=100.0),  # 20 % slower: a jump
        "c": replace(PLAIN, bpm=200.0),  # double time of 100: compatible
    }
    good, found = hints_of(facts, ("a", 0, 240.0), ("b", 0, 240.0), ("c", 0, 240.0), fades=[8, 8])
    assert [(h.code, h.pieces, h.bpm) for h in found] == [("tempo_jump", (0, 1), (120.0, 100.0))]
    assert found[0].text().startswith("tempo_jump: pieces 0 and 1 are at 120 and 100 BPM")
    assert all(set(t) == {"reference", "start_sec", "end_sec"} for t in good["tracks"])
    close = {**facts, "b": replace(PLAIN, bpm=114.0)}  # 5 % apart: within 6 %
    _, found = hints_of(close, ("a", 0, 240.0), ("b", 0, 240.0), ("c", 0, 240.0), fades=[8, 8])
    assert [h.code for h in found] == ["tempo_jump"]  # 114 to 200: neither close nor double
    assert found[0].pieces == (1, 2)


def test_per_transition_the_tempo_hint_comes_before_the_lock_hint() -> None:
    facts = {"a": track(), "b": track(bpm=100.0, first=0.3), "c": track(bpm=100.0, first=0.3)}
    _, found = hints_of(facts, ("a", 0, 200.5), ("b", 2.7, 240.0), ("c", 0, 240.0), fades=[8, 0])
    assert [h.code for h in found] == ["tempo_jump", "downbeats_not_locked"]
    assert {h.pieces for h in found} == {(0, 1)}


# -- regression: snapped transitions (the one duration rule, crossfades, 10 %) -----------------


def snapped() -> dict[str, Any]:
    a = replace(PLAIN, timing_class="exact", candidates=END_POINTS)
    b = replace(PLAIN, timing_class="exact", candidates=START_POINTS)
    facts = {"a": a, "b": b, "c": PLAIN}
    return checked(
        spec(("a", 0, 201.4), ("b", 30.8, 240.0), ("c", 0, 240.0), crossfades=[8, 8]), ORDER, facts
    )


def test_the_effective_duration_with_snapped_transitions_is_the_librarys() -> None:
    good = snapped()
    assert cuts_of(good)[:2] == [(0.0, 202.5), (32.5, 240.0)]
    windows = [t["end_sec"] - t["start_sec"] for t in good["tracks"]]
    total = total_sec(windows, good["crossfades"])
    assert total == 202.5 + 207.5 + 240.0 - 16
    maps = [
        {
            "duration": 240.0,
            "songs": [{"start": 0, "end": 240.0, "bpm": 120.0, "first_downbeat": 0.5}],
            "events": [],
        }
        for _ in range(3)
    ]
    assert compose.compose_map(maps, good)["duration"] == total


def test_the_crossfade_rules_hold_after_a_transition_snap() -> None:
    a = replace(PLAIN, timing_class="exact", candidates=(point(62.2, "phrase_end"),))
    facts = {"a": a, "b": PLAIN, "c": PLAIN}
    # a middle piece 2.5 to 61.4: its end moves to the phrase end, 62.5, so 60 s are played
    good = checked(
        spec(("b", 0, 240.0), ("a", 2.5, 61.4), ("c", 0, 240.0), crossfades=[29, 30]), ORDER, facts
    )
    assert cuts_of(good)[1] == (2.5, 62.5)
    with pytest.raises(MixPlanRefused) as caught:
        checked(
            spec(("b", 0, 240.0), ("a", 2.5, 61.4), ("c", 0, 240.0), crossfades=[30, 30]),
            ORDER,
            facts,
        )
    assert caught.value.reason == "crossfades_overlap"  # checked on the snapped window


def test_the_ten_percent_class_length_tolerance_counts_the_snapped_windows() -> None:
    good = snapped()
    windows = [t["end_sec"] - t["start_sec"] for t in good["tracks"]]
    total = total_sec(windows, good["crossfades"])  # 634 s
    assert within_tolerance(total, 600)
    assert not within_tolerance(total, 570)  # 634 is over 627
