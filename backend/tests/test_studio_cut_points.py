"""#1328: a studio track's stored cut candidates on their way to the check and to the model. The
trust is inherited again from the track's stored timing class (``StudioTrack.facts``), the model
sees the points by track index with hints marked as hints and no downbeats, the prompts keep the
whole track by default, and a plan cut near a trusted point comes back on it. Pure: no I/O."""

from typing import Any

import pytest

from classbeat.agent import studio_mix, studio_prompt
from classbeat.agent.studio_mix import StudioTrack
from classbeat.agent.studio_schemas import MixInput
from classbeat.spotdown.cut_candidates_model import MAP_KEY, Candidate, as_json
from classbeat.spotdown.studio_map_model import stored_timing_class
from studiomaps import track_map, whole_mix

POINTS = (
    Candidate(0.0, "silence_start", 0.9, "measured", True),
    Candidate(0.3, "content_start", 0.95, "measured", True),
    Candidate(0.4, "downbeat", 0.8, "estimated", True),
    Candidate(30.4, "label_start", 0.3, "heuristic", False, "intro"),
    Candidate(31.5, "segment_start", 0.8, "estimated", True),
    Candidate(150.4, "phrase_start", 0.4, "heuristic", False),
    Candidate(296.0, "fade_out_start", 0.8, "measured", True),
)


def with_points(timing: Any, points: object = None) -> StudioTrack:
    stored = as_json(POINTS) if points is None else points
    return StudioTrack("Song", "Band", {**track_map(300.0), MAP_KEY: stored}, timing)


def test_an_exact_track_keeps_its_stored_candidates() -> None:
    facts = with_points("exact").facts()
    assert (facts.timing_class, facts.candidates) == ("exact", POINTS)


def test_an_approximate_track_has_advisory_candidates_only() -> None:
    facts = with_points("approximate").facts()
    assert [c.at_s for c in facts.candidates] == [c.at_s for c in POINTS]
    assert not any(c.trusted for c in facts.candidates)


@pytest.mark.parametrize(
    ("timing", "points"),
    [
        ("unusable", None),
        (None, None),  # a local analysis: no stored timing decision
        ("exact", [*as_json(POINTS), {"at_s": 1.0, "kind": "drop", "trusted": True}]),
        ("exact", "points"),
    ],
)
def test_no_candidates_without_a_usable_timing_class_or_a_whole_stored_list(
    timing: Any, points: object
) -> None:
    assert with_points(timing, points).facts().candidates == ()


def test_the_stored_timing_class_is_the_records_and_any_doubt_is_unusable() -> None:
    assert stored_timing_class(None) is None
    assert stored_timing_class({"timing": {"class": "exact"}}) == "exact"
    assert stored_timing_class({"timing": {"class": "approximate"}}) == "approximate"
    for doubt in ({}, {"timing": "exact"}, {"timing": {"class": "EXACT"}}, "exact"):
        assert stored_timing_class(doubt) == "unusable"


def test_the_model_sees_the_edges_in_the_summary_and_every_point_in_a_detail() -> None:
    tracks = (with_points("exact"), StudioTrack("Local", "Band", track_map(300.0)))
    summary = studio_mix.music_summary(tracks)["tracks"]
    assert summary[0]["timing"] == "exact"
    assert summary[0]["cut_points"] == [
        {"at_sec": 0.0, "kind": "silence_start", "use": "trusted"},
        {"at_sec": 0.3, "kind": "content_start", "use": "trusted"},
        {"at_sec": 296.0, "kind": "fade_out_start", "use": "trusted"},
    ]
    assert "timing" not in summary[1]
    assert "cut_points" not in summary[1]
    detail = studio_mix.track_detail(tracks, 0)["cut_points"]
    assert [p["kind"] for p in detail] == [
        "silence_start", "content_start", "label_start", "segment_start", "phrase_start",
        "fade_out_start",
    ]  # fmt: skip
    assert {"at_sec": 30.4, "kind": "label_start", "use": "hint", "label": "intro"} in detail
    assert {"at_sec": 150.4, "kind": "phrase_start", "use": "hint"} in detail
    approximate = studio_mix.track_detail((with_points("approximate"),), 0)["cut_points"]
    assert {p["use"] for p in approximate} == {"hint"}


def test_the_prompts_keep_the_whole_track_by_default() -> None:
    for system in (studio_prompt.DESIGN_SYSTEM, studio_prompt.REVISE_SYSTEM):
        assert "keep intros, outros and fades" in system
        assert "cut only actual silence" in system
        assert "give the reason in that piece's why" in system
        assert "A point whose use is hint is a guess" in system
        assert "timing approximate or unusable is never moved to a bar" in system


def test_a_plan_cut_near_a_trusted_point_comes_back_on_it() -> None:
    tracks = (with_points("exact"), *[StudioTrack("Song", "Band", track_map(310.0))])
    mix = whole_mix(2)
    mix["tracks"][0] |= {"start_sec": 30.9, "why": "A shorter intro for the warm-up."}
    composed = studio_mix.compose(tracks, MixInput.model_validate(mix))
    # 30.9 selects the segment start at 31.5, which snaps to the bar at 32.27 (0.4 + 17 bars)
    assert composed.plan["tracks"][0]["start_sec"] == 32.27
    plain = studio_mix.compose(
        (StudioTrack("Song", "Band", track_map(300.0)), tracks[1]), MixInput.model_validate(mix)
    )
    assert (
        plain.plan["tracks"][0]["start_sec"] == 30.4
    )  # no candidate: the nearest bar (0.4 + 16 bars)
