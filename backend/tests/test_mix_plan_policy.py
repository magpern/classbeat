"""SR C5 (ADR 0036 section 4.2): the mix spec's rules, each refused with its named reason, the cuts
snapped to the analysis's downbeats and the one duration rule (``studio.mix_plan_policy``)."""

from typing import Any

import pytest
from mowl import compose

from classbeat.studio.mix_plan_policy import (
    MAX_TOTAL_SEC,
    MixPlanRefused,
    TrackFacts,
    checked,
    total_sec,
)
from classbeat.studio.mix_spec_model import MixSpec

BAR = 240.0 / 120.0  # 2 s bars at 120 BPM
FACTS = {r: TrackFacts(length=240.0, bpm=120.0, first_downbeat=0.5) for r in ("a", "b", "c")}
ORDER = ["a", "b", "c"]


def spec(*cuts: tuple[str, Any, Any], crossfades: list[Any] | None = None) -> dict[str, Any]:
    tracks = [{"reference": r, "start_sec": s, "end_sec": e} for r, s, e in cuts]
    return {
        "tracks": tracks,
        "crossfades": [8] * (len(cuts) - 1) if crossfades is None else crossfades,
    }


WHOLE = spec(("b", 0, 240.0), ("a", 0, 240.0), ("c", 0, 240.0))


def refused(value: dict[str, Any], approved: list[str | None] | None = None) -> str:
    with pytest.raises(MixPlanRefused) as caught:
        checked(value, ORDER if approved is None else approved, FACTS)
    return caught.value.reason


def test_a_whole_permutation_passes_with_its_edges_kept() -> None:
    good = checked(WHOLE, ORDER, FACTS)
    assert [t["reference"] for t in good["tracks"]] == ["b", "a", "c"]
    assert [(t["start_sec"], t["end_sec"]) for t in good["tracks"]] == [(0.0, 240.0)] * 3
    assert good["crossfades"] == [8, 8]
    MixSpec.model_validate(good)  # the stored shape


def test_cuts_snap_to_the_nearest_downbeat_rounded_to_a_hundredth() -> None:
    good = checked(spec(("a", 10.3, 200.9), ("b", 0, 240.0), ("c", 0, 240.0)), ORDER, FACTS)
    first = good["tracks"][0]
    # 0.5 + n * 2: 10.3 -> 10.5, 200.9 -> 200.5
    assert (first["start_sec"], first["end_sec"]) == (10.5, 200.5)
    assert checked(good, ORDER, FACTS) == good  # snapping a snapped spec changes nothing


def test_the_one_duration_rule_matches_the_library() -> None:
    good = checked(spec(("a", 10.3, 200.9), ("b", 0, 240.0), ("c", 0, 240.0)), ORDER, FACTS)
    windows = [t["end_sec"] - t["start_sec"] for t in good["tracks"]]
    maps = [
        {"duration": 240.0, "songs": [{"start": 0, "end": 240.0, "bpm": 120.0,
                                       "first_downbeat": 0.5}], "events": []}
        for _ in range(3)
    ]  # fmt: skip
    assert compose.compose_map(maps, good)["duration"] == total_sec(windows, good["crossfades"])


@pytest.mark.parametrize(
    ("value", "reason"),
    [
        (spec(("a", 0, 240.0), ("b", 0, 240.0)), "not_a_permutation"),  # c missing
        (spec(("a", 0, 240.0), ("a", 0, 240.0), ("b", 0, 240.0)), "not_a_permutation"),
        (spec(("a", 0, 240.0), ("b", 0, 240.0), ("z", 0, 240.0)), "not_a_permutation"),
        ({"tracks": [], "crossfades": []}, "not_a_permutation"),
        (spec(("a", -1, 240.0), ("b", 0, 240.0), ("c", 0, 240.0)), "cut_out_of_range"),
        (spec(("a", 0, 241.0), ("b", 0, 240.0), ("c", 0, 240.0)), "cut_out_of_range"),
        (spec(("a", 100, 90), ("b", 0, 240.0), ("c", 0, 240.0)), "cut_out_of_range"),
        (spec(("a", "0", 240.0), ("b", 0, 240.0), ("c", 0, 240.0)), "cut_out_of_range"),
        (spec(("a", 0, 59.0), ("b", 0, 240.0), ("c", 0, 240.0)), "window_too_short"),
        (spec(("a", 0, 240.0), ("b", 0, 240.0), ("c", 0, 240.0), crossfades=[8]),
         "crossfade_count"),
        (spec(("a", 0, 240.0), ("b", 0, 240.0), ("c", 0, 240.0), crossfades=[8, 2.5]),
         "crossfade_not_whole"),
        (spec(("a", 0, 240.0), ("b", 0, 240.0), ("c", 0, 240.0), crossfades=[8, True]),
         "crossfade_not_whole"),
        (spec(("a", 0, 240.0), ("b", 0, 240.0), ("c", 0, 240.0), crossfades=[8, 31]),
         "crossfade_out_of_range"),
        (spec(("a", 0, 240.0), ("b", 0, 240.0), ("c", 0, 240.0), crossfades=[-1, 8]),
         "crossfade_out_of_range"),
    ],
)  # fmt: skip
def test_each_rule_is_refused_with_its_reason(value: dict[str, Any], reason: str) -> None:
    assert refused(value) == reason


def test_a_middle_tracks_crossfades_leave_part_of_it_alone() -> None:
    one_minute = {k: TrackFacts(length=60.0, bpm=120.0, first_downbeat=0.5) for k in ORDER}
    edges = spec(*[(r, 0, 60.0) for r in ORDER], crossfades=[30, 29])
    assert checked(edges, ORDER, one_minute)["crossfades"] == [30, 29]  # 59 < 60
    with pytest.raises(MixPlanRefused) as caught:
        checked(spec(*[(r, 0, 60.0) for r in ORDER], crossfades=[30, 30]), ORDER, one_minute)
    assert caught.value.reason == "crossfades_overlap"


def test_a_crossfade_as_long_as_a_neighbours_window_is_too_long() -> None:
    """With 60 s played at least and 30 s at most, ``checked`` cannot reach this rule; it is
    kept as the ADR states it, so a change of either bound cannot drop it silently."""
    from classbeat.studio import mix_plan_policy

    with pytest.raises(MixPlanRefused) as caught:
        mix_plan_policy._crossfades([30], [30.0, 240.0])  # pyright: ignore[reportPrivateUsage]
    assert caught.value.reason == "crossfade_too_long"
    with pytest.raises(MixPlanRefused) as caught:
        mix_plan_policy._crossfades([20], [240.0, 20.0])  # pyright: ignore[reportPrivateUsage]
    assert caught.value.reason == "crossfade_too_long"


def test_a_gap_in_the_approved_list_is_refused() -> None:
    assert refused(WHOLE, ["a", None, "b", "c"]) == "list_has_gap"


@pytest.mark.parametrize("field", ["tempo", "rate", "bpm", "speed", "pitch"])
def test_a_tempo_field_is_a_tempo_change(field: str) -> None:
    value = spec(("a", 0, 240.0), ("b", 0, 240.0), ("c", 0, 240.0))
    value["tracks"][0][field] = 1.05
    assert refused(value) == "tempo_change"


def test_any_other_field_is_unknown() -> None:
    value = spec(("a", 0, 240.0), ("b", 0, 240.0), ("c", 0, 240.0))
    value["tracks"][0]["length"] = 999.0  # never a length from outside the analysis
    assert refused(value) == "unknown_field"
    assert refused({**WHOLE, "total_sec": 1}) == "unknown_field"


def test_at_most_forty_tracks_and_150_minutes() -> None:
    many = {f"t{n}": TrackFacts(length=240.0, bpm=120.0, first_downbeat=0.5) for n in range(41)}
    over = spec(*[(r, 0, 240.0) for r in many])
    with pytest.raises(MixPlanRefused) as caught:
        checked(over, list(many), many)
    assert caught.value.reason == "too_many_tracks"
    long = {f"t{n}": TrackFacts(length=300.0, bpm=120.0, first_downbeat=0.5) for n in range(31)}
    value = spec(*[(r, 0, 300.0) for r in long], crossfades=[0] * 30)
    assert MAX_TOTAL_SEC < 31 * 300
    with pytest.raises(MixPlanRefused) as caught:
        checked(value, list(long), long)
    assert caught.value.reason == "mix_too_long"
    fits = spec(*[(r, 0, 300.0) for r in list(long)[:30]], crossfades=[0] * 29)
    assert checked(fits, list(long)[:30], long)  # exactly 150 minutes


def test_the_stored_shape_refuses_unrounded_cuts_and_a_wrong_count() -> None:
    with pytest.raises(ValueError, match=r"0\.01"):
        MixSpec.model_validate(spec(("a", 0.123, 240.0)))
    with pytest.raises(ValueError, match="one crossfade"):
        MixSpec.model_validate(spec(("a", 0, 240.0), ("b", 0, 240.0), crossfades=[]))
    with pytest.raises(ValueError, match="less than or equal to 30"):
        MixSpec.model_validate(spec(("a", 0, 240.0), ("b", 0, 240.0), crossfades=[31]))
