"""#1342 part 1 (ADR 0036 amendment "a song may appear more than once in a mix", A2, A3, A5 to A7):
plain repeats in the mix spec. Coverage, the per-piece check structure, the loop join refused
until part 2, the ordinary rules on repeated pieces, and the soft hints. Pure: no I/O."""

from typing import Any

import pytest
from mowl import compose

from classbeat.agent.songs import total_sec as list_total_sec
from classbeat.studio import mix_hint_policy, mix_repeat_calc
from classbeat.studio.mix_hint_policy import Hint, hints
from classbeat.studio.mix_plan_policy import (
    COVERAGE,
    MAX_TOTAL_SEC,
    MixPlanRefused,
    TrackFacts,
    checked,
    cs,
    total_sec,
)
from classbeat.studio.mix_spec_model import MixSpec
from classbeat.swedebeat_studio.length_policy import studio_total, within

FACTS = {r: TrackFacts(length=240.0, bpm=120.0, first_downbeat=0.5) for r in ("a", "b", "c")}
ORDER = ["a", "b", "c"]
LENGTHS = {r: f.length for r, f in FACTS.items()}


def spec(*cuts: tuple[str, Any, Any], crossfades: list[Any] | None = None) -> dict[str, Any]:
    tracks = [{"reference": r, "start_sec": s, "end_sec": e} for r, s, e in cuts]
    return {
        "tracks": tracks,
        "crossfades": [8] * (len(cuts) - 1) if crossfades is None else crossfades,
    }


def refusal(value: dict[str, Any], facts: dict[str, TrackFacts] | None = None) -> MixPlanRefused:
    with pytest.raises(MixPlanRefused) as caught:
        checked(value, ORDER, FACTS if facts is None else facts)
    return caught.value


def maps(good: dict[str, Any], facts: dict[str, TrackFacts]) -> list[dict[str, Any]]:
    """One analysis per piece, the same one again for a repeat (as the callers pass them)."""
    return [
        {"duration": facts[t["reference"]].length,
         "songs": [{"start": 0, "end": facts[t["reference"]].length, "bpm": 120.0,
                    "first_downbeat": 0.5}], "events": []}
        for t in good["tracks"]
    ]  # fmt: skip


# A recovery minute (0.5 to 60.5 s of a) at every recovery: a, b, a, c, a.
MINUTE = ("a", 0.5, 60.5)
RECOVERIES = spec(MINUTE, ("b", 0, 240.0), MINUTE, ("c", 0, 240.0), MINUTE, crossfades=[4] * 4)


# --- coverage and the repeat itself (A2, A3) ----------------------------------------------------


def test_a_non_adjacent_repeat_is_an_ordinary_piece() -> None:
    good = checked(RECOVERIES, ORDER, FACTS)
    assert [t["reference"] for t in good["tracks"]] == ["a", "b", "a", "c", "a"]
    assert good["tracks"][0] == good["tracks"][2] == good["tracks"][4]  # the same window again
    assert checked(good, ORDER, FACTS) == good  # snapping a snapped spec changes nothing
    MixSpec.model_validate(good)  # the stored shape takes it unchanged (A1)


def test_a_repeat_may_take_another_window() -> None:
    value = spec(("a", 0, 120.0), ("b", 0, 240.0), ("a", 120.5, 240.0), ("c", 0, 240.0))
    good = checked(value, ORDER, FACTS)
    assert [(t["start_sec"], t["end_sec"]) for t in good["tracks"]][2] == (120.5, 240.0)


@pytest.mark.parametrize(
    "value",
    [
        spec(("a", 0, 240.0), ("b", 0, 240.0)),  # c missing
        spec(("a", 0, 240.0), ("b", 0, 240.0), ("a", 0, 240.0)),  # a twice, c never
        spec(("a", 0, 240.0), ("b", 0, 240.0), ("c", 0, 240.0), ("z", 0, 240.0)),  # z extra
        spec(("z", 0, 240.0), ("a", 0, 240.0), ("b", 0, 240.0), ("c", 0, 240.0)),
    ],
)
def test_each_approved_track_at_least_once_and_only_those(value: dict[str, Any]) -> None:
    refused = refusal(value)
    assert refused.reason == "not_a_permutation"
    assert str(refused) == COVERAGE == "each approved track at least once, and only those"


def test_an_approved_track_without_an_analysis_is_still_owed() -> None:
    # The validator never drops an approved reference it has no facts for: the mix must still
    # cover it, and a piece of it is refused (the caller always has every analysis).
    some = {r: FACTS[r] for r in ("a", "b")}
    with pytest.raises(MixPlanRefused) as caught:
        checked(spec(("a", 0, 240.0), ("b", 0, 240.0)), ORDER, some)
    assert caught.value.reason == "not_a_permutation"


def test_no_cap_on_repeats_only_on_pieces() -> None:
    many = spec(*[("a", 0.5, 60.5) if n % 2 else ("b", 0.5, 60.5) for n in range(39)],
                ("c", 0.5, 60.5), crossfades=[0] * 39)  # fmt: skip
    assert len(checked(many, ORDER, FACTS)["tracks"]) == 40
    over = {**many, "tracks": [*many["tracks"], many["tracks"][1]], "crossfades": [0] * 40}
    assert refusal(over).reason == "too_many_tracks"


def test_the_total_bound_counts_every_piece() -> None:
    long = {r: TrackFacts(length=600.0, bpm=120.0, first_downbeat=0.5) for r in ORDER}
    pieces = [("a" if n % 3 == 0 else "b" if n % 3 == 1 else "c", 0, 600.0) for n in range(16)]
    value = spec(*pieces, crossfades=[0] * 15)
    assert MAX_TOTAL_SEC < 16 * 600
    assert refusal(value, long).reason == "mix_too_long"
    fits = spec(*pieces[:15], crossfades=[0] * 14)
    assert checked(fits, ORDER, long)  # exactly 150 minutes, repeats counted


# --- the loop join, refused until part 2 (A4.1) --------------------------------------------------


def test_a_loop_join_is_refused_until_the_timing_decision_reaches_the_validator() -> None:
    value = spec(("a", 0.5, 120.5), ("a", 120.5, 240.0), ("b", 0, 240.0), ("c", 0, 240.0),
                 crossfades=[0, 8, 8])  # fmt: skip
    refused = refusal(value)
    assert refused.reason == "loop_needs_exact_timing"
    assert "pieces 0 and 1" in str(refused)
    assert "'a'" not in str(refused)
    assert "reference" not in str(refused)


def test_the_first_loop_join_in_mix_order_is_named() -> None:
    value = spec(("b", 0, 240.0), ("a", 0, 120.5), ("c", 0, 240.0), ("c", 0, 240.0),
                 ("a", 0, 120.5), ("a", 0, 120.5))  # fmt: skip
    assert "pieces 2 and 3" in str(refusal(value))


# --- the fixed order (A7) and the check structure -------------------------------------------------


@pytest.mark.parametrize(
    ("value", "reason"),
    [
        # Two pieces that break two rules: each piece's checks run before the next piece's.
        (spec(("a", 0, 241.0), ("z", 0, 240.0), ("b", 0, 240.0), ("c", 0, 240.0)),
         "cut_out_of_range"),
        (spec(("z", 0, 240.0), ("a", 0, 241.0), ("b", 0, 240.0), ("c", 0, 240.0)),
         "not_a_permutation"),
        # A short window plus a missing track: coverage (step 5) before the lengths (step 7).
        (spec(("a", 0, 59.0), ("b", 0, 240.0)), "not_a_permutation"),
        # A loop join plus a missing track: coverage first.
        (spec(("a", 0, 120.5), ("a", 120.5, 240.0), ("b", 0, 240.0)), "not_a_permutation"),
        # A loop join plus a short window (step 6 before step 7).
        (spec(("a", 0, 30.5), ("a", 30.5, 240.0), ("b", 0, 240.0), ("c", 0, 240.0)),
         "loop_needs_exact_timing"),
        # A short repeat plus a wrong crossfade count: the lengths before the crossfades.
        (spec(("a", 0, 240.0), ("b", 0, 240.0), ("a", 0, 30.5), ("c", 0, 240.0),
              crossfades=[8]), "window_too_short"),
        # A loop join plus a crossfade out of range: the loop join first.
        (spec(("a", 0, 120.5), ("a", 120.5, 240.0), ("b", 0, 240.0), ("c", 0, 240.0),
              crossfades=[31, 8, 8]), "loop_needs_exact_timing"),
        # A cut that snaps to nothing (10.3 and 10.9 both to the downbeat 10.5): A7 step 4.
        (spec(("a", 10.3, 10.9), ("b", 0, 240.0), ("c", 0, 240.0)), "cut_out_of_range"),
    ],
)  # fmt: skip
def test_the_first_reason_follows_the_fixed_order(value: dict[str, Any], reason: str) -> None:
    assert refusal(value).reason == reason


# --- the ordinary rules with repeats (A3, A6) -----------------------------------------------------


def test_the_effective_duration_counts_every_piece_and_matches_the_library() -> None:
    good = checked(RECOVERIES, ORDER, FACTS)
    windows = [t["end_sec"] - t["start_sec"] for t in good["tracks"]]
    assert windows == [60.0, 240.0, 60.0, 240.0, 60.0]
    expected = 3 * 60.0 + 2 * 240.0 - 4 * 4
    assert total_sec(windows, good["crossfades"]) == expected
    assert compose.compose_map(maps(good, FACTS), good)["duration"] == expected
    offsets = compose.mix_offsets(
        good, 44100, [FACTS[t["reference"]].length for t in good["tracks"]]
    )
    assert len(offsets) == 5  # one render entry per piece
    assert [o["src_start"] for o in offsets][0::2] == [round(0.5 * 44100)] * 3


@pytest.mark.parametrize(
    ("crossfades", "reason"),
    [
        ([30, 29, 30, 29], None),  # 30 + 29 < 60 s of each middle repeat
        ([4, 30, 30, 4], "crossfades_overlap"),  # a repeat's incoming plus outgoing is its window
        ([4, 4, 4], "crossfade_count"),  # one per change of piece, repeats counted
        ([4, 4, 2.5, 4], "crossfade_not_whole"),
        ([4, 4, 31, 4], "crossfade_out_of_range"),
    ],
)
def test_the_crossfade_rules_hold_per_piece(crossfades: list[Any], reason: str | None) -> None:
    value = {**RECOVERIES, "crossfades": crossfades}
    if reason is None:
        assert checked(value, ORDER, FACTS)["crossfades"] == crossfades
    else:
        assert refusal(value).reason == reason


def test_a_short_repeat_is_refused_like_any_piece() -> None:
    value = spec(("a", 0, 240.0), ("b", 0, 240.0), ("a", 0.5, 58.5), ("c", 0, 240.0))
    refused = refusal(value)
    assert refused.reason == "window_too_short"


def test_the_ten_percent_tolerance_is_the_lists_and_repeats_never_lift_it() -> None:
    # A6: the song stage checks the approved list, each song once; the composed duration counts
    # every piece. Three 4-minute songs are 11.7 minutes as a list: under a 30-minute class's
    # lower bound (27 min), however often the mix repeats them.
    target = 30 * 60
    listed = studio_total([240, 240, 240])
    assert listed == round(list_total_sec([240, 240, 240]))
    assert not within(listed, target)
    looped = spec(*[(r, 0, 240.0) for r in (ORDER * 3)[:8]], crossfades=[8] * 7)
    good = checked(looped, ORDER, FACTS)
    composed = total_sec(
        [t["end_sec"] - t["start_sec"] for t in good["tracks"]], good["crossfades"]
    )
    assert composed == 8 * 240 - 7 * 8  # every piece counted: within 10 % of the target
    assert abs(composed - target) <= 0.1 * target
    assert within(composed, target)


# --- the soft checks (A2, A5) -------------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        # Each song once, however unequal: never flagged (the tightened one_song_dominates).
        (spec(("a", 0, 240.0), ("b", 0.5, 60.5), ("c", 0.5, 60.5)), []),
        (RECOVERIES, []),  # three minutes of a in a 644 s mix
        # a whole, twice: whole_track_repeated, and a fills more than half.
        (spec(("a", 0, 240.0), ("b", 0.5, 60.5), ("a", 0, 240.0), ("c", 0.5, 60.5)),
         [Hint("whole_track_repeated", (0, 2)), Hint("one_song_dominates", (0, 2))]),
        # Repeated parts filling more than half: dominates, not whole.
        (spec(("a", 0.5, 120.5), ("b", 0.5, 60.5), ("a", 0.5, 120.5), ("c", 0.5, 60.5),
              ("a", 0.5, 120.5)), [Hint("one_song_dominates", (0, 2, 4))]),
    ],
)  # fmt: skip
def test_the_hints(value: dict[str, Any], expected: list[Hint]) -> None:
    good = checked(value, ORDER, FACTS)
    assert hints(good, LENGTHS) == expected


def test_whole_is_ninety_percent_of_the_analysed_length() -> None:
    on_zero = {r: TrackFacts(length=240.0, bpm=120.0, first_downbeat=0.0) for r in ORDER}
    whole = spec(("a", 0, 216.0), ("b", 0, 240.0), ("a", 24.0, 240.0), ("c", 0, 240.0))
    good = checked(whole, ORDER, on_zero)
    assert [t["end_sec"] - t["start_sec"] for t in good["tracks"]][0::2] == [216.0, 216.0]
    assert hints(good, LENGTHS) == [Hint("whole_track_repeated", (0, 2))]  # exactly 90 %
    short = spec(("a", 0, 214.0), ("b", 0, 240.0), ("a", 24.0, 240.0), ("c", 0, 240.0))
    assert hints(checked(short, ORDER, on_zero), LENGTHS) == []  # 214 s is under 90 %


def test_one_song_dominates_counts_windows_less_their_outgoing_crossfades() -> None:
    # a: 2 x 120 s, b: 100 s, c: 140 s. With no crossfade a plays 240 of 480 s: half, not more.
    # A 10 s fade out of b shortens the mix to 470 s but not a's 240 s: now more than half.
    value = spec(("a", 0.5, 120.5), ("b", 0.5, 100.5), ("a", 0.5, 120.5), ("c", 0.5, 140.5),
                 crossfades=[0, 0, 0])  # fmt: skip
    good = checked(value, ORDER, FACTS)
    assert hints(good, LENGTHS) == []
    good["crossfades"] = [0, 10, 0]
    assert hints(good, LENGTHS) == [Hint("one_song_dominates", (0, 2))]


def test_a_class_of_one_song_is_never_flagged_as_dominated() -> None:
    # The spec of a one-song class (its loop join is refused in part 1; hints read any spec).
    value = spec(("a", 0, 240.0), ("a", 0, 240.0), crossfades=[8])
    assert [h.code for h in hints(value, {"a": 240.0})] == ["whole_track_repeated"]


def test_hints_name_pieces_never_a_reference() -> None:
    refs = {r: TrackFacts(240.0, 120.0, 0.5) for r in ("ref-AAAA1111", "ref-BBBB2222", "ref-C333")}
    value = {
        "tracks": [
            {"reference": "ref-AAAA1111", "start_sec": 0, "end_sec": 240.0},
            {"reference": "ref-BBBB2222", "start_sec": 0.5, "end_sec": 60.5},
            {"reference": "ref-AAAA1111", "start_sec": 0, "end_sec": 240.0},
            {"reference": "ref-C333", "start_sec": 0.5, "end_sec": 60.5},
        ],
        "crossfades": [4, 4, 4],
    }
    good = checked(value, list(refs), refs)
    found = hints(good, {r: f.length for r, f in refs.items()})
    assert {h.code for h in found} == {"whole_track_repeated", "one_song_dominates"}
    shown = repr([h.as_json() for h in found]) + repr(mix_hint_policy.texts(found))
    assert "ref-" not in shown
    assert all(h.text().startswith(h.code + ": pieces 0, 2 ") for h in found)


# --- the repeat facts (mix_repeat_calc) --------------------------------------------------------


@pytest.mark.parametrize(
    ("references", "repeats", "first", "joins", "runs", "repeat_of"),
    [
        (["a", "b", "c"], False, ["a", "b", "c"], [], [(0, 0), (1, 1), (2, 2)], [None] * 3),
        (["b", "a", "b", "c", "a"], True, ["b", "a", "c"], [], [(0, 0), (1, 1), (2, 2), (3, 3),
         (4, 4)], [None, None, 0, None, 1]),
        (["a", "a", "b", "a", "a", "a"], True, ["a", "b"], [0, 3, 4], [(0, 1), (2, 2), (3, 5)],
         [None, 0, None, 0, 0, 0]),
    ],
)  # fmt: skip
def test_the_repeat_facts(
    references: list[str],
    repeats: bool,
    first: list[str],
    joins: list[int],
    runs: list[tuple[int, int]],
    repeat_of: list[int | None],
) -> None:
    assert mix_repeat_calc.has_repeats(references) is repeats
    assert mix_repeat_calc.first_order(references) == first
    assert mix_repeat_calc.loop_joins(references) == joins
    assert mix_repeat_calc.runs(references) == runs
    assert mix_repeat_calc.repeat_of(references) == repeat_of
    assert cs(1.0) == 100
