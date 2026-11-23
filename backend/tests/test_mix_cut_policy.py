"""#1328 (#580's approved design; ADR 0036 amendment A7): the mix spec's cuts with spotdown's cut
candidates. Per piece the fixed order is the range of the proposed cut, the candidate selection (a
trusted candidate of an ``exact`` track within the tolerance, else the proposed cut), the bar snap
(``exact`` only) and the range again; then coverage, the loop joins,
``MIN_PLAYED_SEC``, the crossfades and the total, all on the placed cuts. Plus the regression
cases of the design: the one duration rule, the crossfade rules and the 10 % class-length
tolerance with trimmed songs. Pure: no I/O."""

import random
from dataclasses import replace
from typing import Any

import pytest
from mowl import compose

from classbeat.agent.songs import within_tolerance
from classbeat.spotdown.cut_candidates_model import Candidate
from classbeat.studio import mix_cut_policy
from classbeat.studio.mix_plan_policy import MixPlanRefused, TrackFacts, checked, cs, total_sec

PLAIN = TrackFacts(
    length=240.0, bpm=120.0, first_downbeat=0.5, timing_class="exact"
)  # 2 s bars on 0.5 + 2n
ORDER = ["a", "b", "c"]


def point(at: float, kind: str = "segment_start", trusted: bool = True) -> Candidate:
    return Candidate(at, kind, 0.8, "estimated", trusted)


def exact(*points: Candidate, timing: Any = "exact") -> TrackFacts:
    return replace(PLAIN, timing_class=timing, candidates=points)


def spec(*cuts: tuple[str, Any, Any], crossfades: list[Any] | None = None) -> dict[str, Any]:
    tracks = [{"reference": r, "start_sec": s, "end_sec": e} for r, s, e in cuts]
    return {
        "tracks": tracks,
        "crossfades": [8] * (len(cuts) - 1) if crossfades is None else crossfades,
    }


def run(a: TrackFacts, *cuts: tuple[str, Any, Any], crossfades: list[Any] | None = None) -> Any:
    facts = {"a": a, "b": PLAIN, "c": PLAIN}
    rest = cuts or (("a", 0, 240.0),)
    with_others = [*rest, *[(r, 0, 240.0) for r in ("b", "c") if r not in {c[0] for c in rest}]]
    return checked(spec(*with_others, crossfades=crossfades), ORDER, facts)


def first(a: TrackFacts, start: Any, end: Any) -> tuple[float, float]:
    t = run(a, ("a", start, end))["tracks"][0]
    return t["start_sec"], t["end_sec"]


def refusal(a: TrackFacts, *cuts: tuple[str, Any, Any], **kw: Any) -> MixPlanRefused:
    with pytest.raises(MixPlanRefused) as caught:
        run(a, *cuts, **kw)
    return caught.value


# -- step 2, the candidate selection ---------------------------------------------------------


def test_a_trusted_candidate_within_the_tolerance_sets_the_cut_then_the_bar_snap() -> None:
    assert mix_cut_policy.tolerance(PLAIN) == pytest.approx(0.9)  # 0.45 of a 2 s bar, under 1 s
    assert first(PLAIN, 30.8, 200.0) == (30.5, 200.5)  # no candidate: the nearest bar
    # 30.8 selects 31.6 (0.8 away), which snaps to 32.5 (0.9 away; 30.5 is 1.1)
    assert first(exact(point(31.6)), 30.8, 200.0) == (32.5, 200.5)


@pytest.mark.parametrize(
    "facts",
    [
        exact(point(31.8)),  # 1.0 s away: out of reach
        exact(point(31.6, trusted=False)),  # a hint
        exact(point(31.6), timing="approximate"),  # advisory only, even if marked trusted
        exact(point(31.6), timing="unusable"),
    ],
)
def test_only_a_trusted_candidate_of_an_exact_track_in_reach_moves_a_cut(facts: TrackFacts) -> None:
    start, _ = first(facts, 30.8, 200.0)
    assert start != 32.5


def test_another_kind_wins_over_a_nearer_downbeat_and_a_tie_goes_to_the_earlier() -> None:
    near_downbeat = exact(point(30.5, "downbeat"), point(31.6, "phrase_start"))
    assert first(near_downbeat, 30.8, 200.0)[0] == 32.5
    tie = exact(point(31.4), point(30.2))  # both 0.6 from 30.8: 30.2 -> 30.5
    assert first(tie, 30.8, 200.0)[0] == 30.5


def test_the_whole_track_stays_whole_silence_intro_outro_and_fades_included() -> None:
    facts = exact(
        point(0.0, "silence_start"), point(0.4, "silence_end"), point(0.4, "content_start"),
        point(0.4, "fade_in_start"), point(8.5, "fade_in_end"), point(8.5, "label_start"),
        point(230.0, "fade_out_start"), point(239.6, "content_end"), point(239.6, "silence_start"),
        point(240.0, "silence_end"), point(240.0, "fade_out_end"),
    )  # fmt: skip
    assert first(facts, 0, 240.0) == (0.0, 240.0)  # no default cut but the model's own
    # the model cut the silence: 0.6 -> 0.4 -> the bar at 0.5; 239.4 -> 239.6 -> 240.5, the length
    assert first(facts, 0.6, 239.4) == (0.5, 240.0)


def test_an_edge_candidate_is_never_selected() -> None:
    assert mix_cut_policy.usable(exact(point(0.0), point(239.995), point(2.0))) == (point(2.0),)


# -- step 3, the snap --------------------------------------------------------------------------


@pytest.mark.parametrize("timing", ["approximate", "unusable"])
def test_a_track_that_is_not_exact_is_never_snapped_to_a_bar(timing: str) -> None:
    facts = exact(point(31.6), point(30.5, "downbeat"), timing=timing)
    assert first(facts, 30.84, 200.006) == (30.84, 200.01)  # rounded to 0.01 s, never moved
    assert first(facts, 0, 239.995) == (0.0, 240.0)  # the edges stay the file's


def test_an_approximate_cut_is_checked_against_the_tracks_own_facts() -> None:
    facts = exact(point(239.0), timing="approximate")
    assert refusal(facts, ("a", 0, 241.0)).reason == "cut_out_of_range"  # its own length
    assert refusal(facts, ("a", 180.0, 239.0)).reason == "window_too_short"  # 59 s played


# -- the fixed order -------------------------------------------------------------------------


def test_the_range_of_the_proposed_cut_comes_before_the_candidates() -> None:
    caught = refusal(exact(point(239.5)), ("a", 0, 240.5))  # a candidate in reach of 240.5
    assert (caught.reason, str(caught)) == (
        "cut_out_of_range",
        "need 0 <= start_sec < end_sec <= length",
    )


def test_a_cut_left_with_no_window_once_placed_is_refused_by_name() -> None:
    # 61.0 selects 61.6, which snaps to 62.5; 62.0 snaps to 62.5: nothing plays
    caught = refusal(exact(point(61.6)), ("a", 61.0, 62.0))
    assert caught.reason == "cut_out_of_range"
    assert str(caught) == "the placed cut (candidate, snap) leaves no window"
    assert refusal(PLAIN, ("a", 61.0, 62.0)).reason == "window_too_short"  # 60.5 to 62.5


def test_a_pieces_own_checks_run_before_the_next_pieces() -> None:
    facts = {"a": exact(point(61.6)), "b": PLAIN, "c": PLAIN}
    value = spec(("a", 61.0, 62.0), ("z", 0, 240.0), ("b", 0, 240.0), ("c", 0, 240.0))
    with pytest.raises(MixPlanRefused) as caught:
        checked(value, ORDER, facts)
    assert caught.value.reason == "cut_out_of_range"  # piece 0's step 4 before piece 1


def test_a_loop_join_on_an_exact_track_lands_on_its_phrase_point() -> None:
    # Part 2 of #1342: 120.0 selects the trusted phrase start 120.5 (A7 step 2) on both sides.
    facts = exact(point(61.6), point(120.5, "phrase_start"))
    good = run(facts, ("a", 0, 120.0), ("a", 120.0, 240.0), crossfades=[0, 8, 8])
    assert [(t["start_sec"], t["end_sec"]) for t in good["tracks"][:2]] == [
        (0, 120.5),
        (120.5, 240.0),
    ]
    approximate = replace(facts, timing_class="approximate")
    caught = refusal(approximate, ("a", 0, 120.0), ("a", 120.0, 240.0), crossfades=[0, 8, 8])
    assert caught.reason == "loop_needs_exact_timing"


@pytest.mark.parametrize("seed", range(20))
def test_checking_a_checked_spec_changes_nothing(seed: int) -> None:
    rng = random.Random(seed)  # noqa: S311 - a reproducible table, not a secret
    bpm = rng.uniform(70.0, 190.0)
    length = rng.uniform(150.0, 400.0)
    points = tuple(
        point(round(rng.uniform(0.0, length), 3), rng.choice(["segment_start", "downbeat"]),
              rng.random() < 0.8)
        for _ in range(rng.randint(0, 80))
    )  # fmt: skip
    facts = {
        "a": TrackFacts(length, bpm, rng.uniform(0.0, 2.0), "exact", (*points, point(0.0))),
        "b": PLAIN,
        "c": PLAIN,
    }
    for _ in range(25):
        start = round(rng.uniform(0.0, length / 2), 2)
        end = round(rng.uniform(start + 61.0, length + 0.005), 2)
        value = spec(("a", start, end), ("b", 0, 240.0), ("c", 0, 240.0))
        try:
            good = checked(value, ORDER, facts)
        except MixPlanRefused:
            continue
        assert checked(good, ORDER, facts) == good


# -- regression: trimmed songs (#580's design, rule 5) ---------------------------------------


def trimmed() -> dict[str, Any]:
    # a: 30.8 -> 31.6 -> 32.5 and 150.2 -> 149.4 -> 148.5 (candidates); b and c whole
    return run(exact(point(31.6), point(149.4, "phrase_start")), ("a", 30.8, 150.2))


def test_the_effective_duration_of_trimmed_songs_is_the_librarys() -> None:
    good = trimmed()
    assert (good["tracks"][0]["start_sec"], good["tracks"][0]["end_sec"]) == (32.5, 148.5)
    windows = [t["end_sec"] - t["start_sec"] for t in good["tracks"]]
    maps = [
        {"duration": 240.0, "songs": [{"start": 0, "end": 240.0, "bpm": 120.0,
                                       "first_downbeat": 0.5}], "events": []}
        for _ in range(3)
    ]  # fmt: skip
    total = total_sec(windows, good["crossfades"])
    assert total == 116.0 + 240.0 + 240.0 - 16
    assert compose.compose_map(maps, good)["duration"] == total


def test_the_crossfade_rules_hold_on_the_trimmed_windows() -> None:
    # a middle piece 2.5 to 64.2 plays 62 s (64.5): 30 + 30 s of crossfade leaves part alone
    order = (("b", 0, 240.0), ("a", 2.5, 64.2), ("c", 0, 240.0))
    assert run(PLAIN, *order, crossfades=[30, 30])["tracks"][1]["end_sec"] == 64.5
    # with a candidate at 63.4 the end lands on 62.5: 60 s, no longer longer than 30 + 30
    caught = refusal(exact(point(63.4)), *order, crossfades=[30, 30])
    assert caught.reason == "crossfades_overlap"
    good = run(exact(point(63.4)), *order, crossfades=[29, 30])
    assert cs(good["tracks"][1]["end_sec"]) - cs(good["tracks"][1]["start_sec"]) == 6000


def test_the_ten_percent_class_length_tolerance_counts_the_trimmed_windows() -> None:
    whole = run(PLAIN)
    target = 600
    whole_total = total_sec([240.0] * 3, whole["crossfades"])
    assert not within_tolerance(whole_total, target)  # 704 s is over 660 s
    good = trimmed()
    windows = [t["end_sec"] - t["start_sec"] for t in good["tracks"]]
    assert within_tolerance(total_sec(windows, good["crossfades"]), target)  # 580 s


def test_a_bar_within_the_cut_rounding_of_the_end_is_the_end() -> None:
    # 1.993 + 99 bars of 2 s = 199.993: rounded 199.99, one hundredth from the 200 s end
    facts = TrackFacts(200.0, 120.0, 1.993, "exact")
    assert mix_cut_policy.placed(199.5, facts) == 200.0
    others = {"b": PLAIN, "c": PLAIN}
    value = spec(("a", 0, 199.5), ("b", 0, 240.0), ("c", 0, 240.0))
    good = checked(value, ORDER, {"a": facts, **others})
    assert good["tracks"][0]["end_sec"] == 200.0
    assert checked(good, ORDER, {"a": facts, **others}) == good


@pytest.mark.parametrize("seed", range(40))
def test_cuts_near_the_end_place_once_on_any_grid(seed: int) -> None:
    rng = random.Random(1000 + seed)  # noqa: S311 - a reproducible table, not a secret
    length = round(rng.uniform(150.0, 400.0), rng.choice([2, 3]))
    bpm = rng.uniform(70.0, 190.0)
    bar = 240.0 / bpm
    # every other grid has a downbeat just before the end, within the cut rounding of it
    first = (length - rng.uniform(0.004, 0.014)) % bar if seed % 2 else rng.uniform(0.0, 2.0)
    facts = TrackFacts(length, bpm, first, "exact")
    for _ in range(25):
        at = round(rng.uniform(length - 3.0, length + 0.005), rng.choice([2, 3]))
        once = mix_cut_policy.placed(at, facts)
        assert mix_cut_policy.placed(once, facts) == once
