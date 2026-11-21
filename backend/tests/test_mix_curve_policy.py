"""#1330: which transition curve the rendered mix takes (``mix_curve_policy``), the render plan
that carries it and the playlist body that never does (``output_body_policy``), and the low-band
energy curve it reads (``spotdown.low_band_model``, stored with a studio map, read by
``mix_facts_model.of_map``). Pure: no I/O."""

import copy
from dataclasses import replace
from typing import Any

import pytest

from classbeat.spotdown import low_band_model
from classbeat.spotdown.studio_map_model import STUDIO_POLICY_VERSIONS, StudioMap, studio_map
from classbeat.studio import mix_curve_policy as mc
from classbeat.studio import output_body_policy as body
from classbeat.studio import render_policy
from classbeat.studio.mix_facts_model import TrackFacts, of_map
from classbeat.studio.render_policy import SWAP_RAMP_FRAMES, validate_plan
from spotdownfake import example
from test_mix_transition_policy import track

RATE = body.RATE
LOUD: low_band_model.Curve = (1.0, tuple([0.8] * 241))
QUIET: low_band_model.Curve = (1.0, tuple([0.05] * 241))


def bassy(facts: TrackFacts, curve: low_band_model.Curve | None = LOUD) -> TrackFacts:
    return replace(facts, low_band=curve)


A, B = bassy(track()), bassy(track(first=0.25))  # 120 BPM, downbeats on 0.5 + 2n and 0.25 + 2n


# -- the choice --------------------------------------------------------------------------------


def test_a_locked_overlap_with_bass_on_both_sides_takes_a_bass_swap_on_its_middle_downbeat() -> (
    None
):
    # 8 s from 10.25: the incoming downbeats inside are 2, 4 and 6 s in; the middle is 4 s
    assert mc.swap_frames(A, 200.5, B, 10.25, 8) == 4 * RATE
    # 6 s: 2 and 4 s are equally near 3 s: the earlier
    assert mc.swap_frames(A, 200.5, B, 10.25, 6) == 2 * RATE


@pytest.mark.parametrize(
    ("a", "b", "end", "fade"),
    [
        (A, B, 200.5, 7),  # not locked (3.5 bars)
        (A, B, 200.5, 0),  # no overlap
        (bassy(track(), QUIET), B, 200.5, 8),  # no real bass going out
        (A, bassy(track(first=0.25), QUIET), 200.5, 8),  # none coming in
        (bassy(track(), None), B, 200.5, 8),  # the low band is not known
        (replace(A, timing_class="approximate"), B, 200.5, 8),  # not exact: no lock to check
        (A, B, 200.5, 1),  # 1 s: no incoming downbeat inside the overlap after the handover
    ],
)
def test_anything_else_stays_equal_power(
    a: TrackFacts, b: TrackFacts, end: float, fade: int
) -> None:
    assert mc.swap_frames(a, end, b, 10.25, fade) is None


def test_the_bass_threshold_is_the_mean_over_each_sides_part_of_the_overlap() -> None:
    assert mc.BASS_MIN == 0.3
    values = [0.8] * 241
    values[193:201] = [0.29] * 8  # the outgoing side, 192.5 to 200.5 (193 to 200): under it
    assert mc.swap_frames(bassy(track(), (1.0, tuple(values))), 200.5, B, 10.25, 8) is None
    values[193:201] = [0.3] * 8  # at it
    assert mc.swap_frames(bassy(track(), (1.0, tuple(values))), 200.5, B, 10.25, 8) == 4 * RATE


def test_the_swap_is_at_least_the_handover_into_the_overlap_and_inside_it() -> None:
    swap = mc.swap_frames(A, 200.5, B, 10.25, 2)  # one incoming downbeat, at the overlap's end
    assert swap is None
    assert SWAP_RAMP_FRAMES < 2 * RATE


# -- the render plan and the playlist body -------------------------------------------------------

SPEC = {
    "tracks": [
        {"reference": "a", "start_sec": 0.0, "end_sec": 200.5},
        {"reference": "b", "start_sec": 10.25, "end_sec": 240.0},
        {"reference": "c", "start_sec": 0.0, "end_sec": 240.0},
    ],
    "crossfades": [8, 7],
}
FACTS = {"a": A, "b": B, "c": bassy(track(first=0.3))}
DURATIONS = {"a": 240.0, "b": 240.0, "c": 240.0}


def test_the_render_plan_names_a_bass_swap_only_where_one_is_chosen() -> None:
    plan = body.render_plan(SPEC, DURATIONS, FACTS)
    assert plan[1]["curve"] == "bass_swap"
    assert plan[1]["swap"] == 4 * RATE
    assert "curve" not in plan[0]
    assert "curve" not in plan[2]
    tracks = [
        {**{k: v for k, v in p.items() if k != "reference"}, "path": f"t{n}"}
        for n, p in enumerate(plan)
    ]
    assert validate_plan({"output": "o", "tracks": tracks})  # the render accepts it


def test_without_facts_or_curves_the_render_plan_is_todays() -> None:
    today = body.render_plan(SPEC, DURATIONS)
    assert (
        body.render_plan(SPEC, DURATIONS, {k: replace(f, low_band=None) for k, f in FACTS.items()})
        == today
    )
    swapped = body.render_plan(SPEC, DURATIONS, FACTS)
    assert [{k: v for k, v in p.items() if k not in ("curve", "swap")} for p in swapped] == today
    assert body.played_seconds(swapped) == body.played_seconds(today)  # the timing is the same


def test_the_playlist_body_never_carries_a_curve() -> None:
    facts = {r: body.Facts(f"Song {r}", "Band", 120.0) for r in "abc"}
    made = body.playlist_body("Ride", SPEC, facts, DURATIONS)
    assert set(made) == {"name", "category_id", "crossfade_sec", "tracks"}
    assert all("curve" not in t and "swap" not in t for t in made["tracks"])
    assert [t["crossfade_sec"] for t in made["tracks"]] == [8, 7, 0]


# -- the low band: read, stored and read back ------------------------------------------------------


def doc(**low_band: Any) -> dict[str, Any]:
    document = copy.deepcopy(example("analysis-v1.minimal.json"))
    document["provenance"]["audio"]["sha256"] = "c" * 64
    document["signal"]["energy"]["low_band"] |= low_band
    return document


def test_only_a_trusted_low_band_is_read() -> None:
    assert low_band_model.curve(doc()) is not None
    assert low_band_model.curve(doc(method="heuristic")) is None
    assert low_band_model.curve(doc(confidence=0.1)) is None
    assert low_band_model.curve(doc(values=[0.3, 1.5])) is None  # off the 0 to 1 scale
    assert low_band_model.curve(doc(values=[])) is None
    assert low_band_model.curve(doc(step_s=0)) is None


@pytest.mark.parametrize(
    "raw",
    [
        None,
        [],
        {"step_s": 1.0},
        {"step_s": True, "values": [0.1]},
        {"step_s": 1.0, "values": [0.1, "x"]},
        {"step_s": 1.0, "values": [float("nan")]},
        {"step_s": 1.0, "values": [0.1] * (low_band_model.MAX_VALUES + 1)},
    ],
)
def test_a_stored_low_band_is_read_whole_or_not_at_all(raw: Any) -> None:
    assert low_band_model.parse(raw) is None


def test_the_studio_map_stores_the_low_band_and_its_policy_version() -> None:
    mapped = studio_map(doc(), "trk-ref-01", "c" * 64, 1)
    assert isinstance(mapped, StudioMap)
    stored = mapped.track_map[low_band_model.MAP_KEY]
    assert low_band_model.parse(stored) == low_band_model.curve(doc())
    assert ("low_band_model", low_band_model.VERSION) in STUDIO_POLICY_VERSIONS
    untrusted = studio_map(doc(method="heuristic"), "trk-ref-01", "c" * 64, 1)
    assert isinstance(untrusted, StudioMap)
    assert low_band_model.MAP_KEY not in untrusted.track_map


def test_the_facts_carry_the_low_band_of_an_exact_track_only() -> None:
    mapped = studio_map(doc(), "trk-ref-01", "c" * 64, 1)
    assert isinstance(mapped, StudioMap)
    assert of_map(mapped.track_map, "exact").low_band == low_band_model.curve(doc())
    for timing in ("approximate", "unusable", None):
        assert of_map(mapped.track_map, timing).low_band is None  # type: ignore[arg-type]


def test_the_mean_is_over_the_values_in_the_span() -> None:
    curve = (1.0, (0.0, 0.2, 0.4, 0.6))
    assert low_band_model.mean(curve, 1.0, 3.0) == pytest.approx(0.3)
    assert low_band_model.mean(curve, 0.2, 0.9) is None


def test_the_render_gets_the_stored_plans_curve_and_nothing_else() -> None:
    plan = body.render_plan(SPEC, DURATIONS, FACTS)
    tracks = [render_policy.track_of({**p, "extra": 1}, f"t{n}") for n, p in enumerate(plan)]
    assert tracks[1]["curve"] == "bass_swap"
    assert tracks[1]["swap"] == 4 * RATE
    assert set(tracks[0]) == {"path", *render_policy.TRACK_INTS}  # no curve: today's track
    assert render_policy.validate_plan({"output": "o", "tracks": tracks})
