"""spotdown's ``analysis-v1`` as classbeat's music data (ADR 0040 sections 3 and 5;
``classbeat.spotdown.spotdown_map_policy``, ``analysis_features_calc``, ``analysis_hints_policy``,
``feature_trust_policy``), on the vendored contract examples."""

import copy
import json
from pathlib import Path
from typing import Any

import pytest
from mowl import spotify, track

from classbeat.design.track_map_policy import valid_track_map
from classbeat.spotdown import feature_trust_policy as ft
from classbeat.spotdown.analysis_hints_policy import Hint, hints
from classbeat.spotdown.spotdown_map_policy import (
    APPROXIMATE_WINDOW_S,
    MappedTrack,
    MapRefused,
    ReferenceSections,
    reference_sections,
    track_facts,
    track_map,
)
from classbeat.spotdown.timing_class_policy import TimingDecision

EXAMPLES = Path(__file__).resolve().parent / "contracts" / "spotdown" / "examples"
EXACT = TimingDecision("exact", "played_recording", {})
APPROXIMATE = TimingDecision("approximate", "matched_recording", {})
UNUSABLE = TimingDecision("unusable", "residual_too_large", {})
SPOTIFY_ID = "4uLU6hMCjMI75M1A2tKUQC"


def example(name: str) -> dict[str, Any]:
    return json.loads((EXAMPLES / name).read_text("utf-8"))


FULL = example("analysis-v1.full.json")
MINIMAL = example("analysis-v1.minimal.json")


def mapped(doc: dict[str, Any]) -> MappedTrack:
    result = track_map(doc, EXACT, track.TRACK_VERSION)
    assert isinstance(result, MappedTrack), result
    return result


# --- trust -----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("feature", "trusted"),
    [
        ({"method": "measured", "confidence": 0.9}, True),
        ({"method": "estimated", "confidence": ft.TRUST_MIN}, True),
        ({"method": "estimated", "confidence": ft.TRUST_MIN - 0.01}, False),
        ({"method": "heuristic", "confidence": 1.0}, False),
        ({"method": "guess", "confidence": 1.0}, False),
        ({"method": "measured"}, False),
        ({"method": "measured", "confidence": True}, False),
        ({"method": "measured", "confidence": float("nan")}, False),
        ({"method": "measured", "confidence": 1.5}, False),
        (None, False),
    ],
)
def test_only_measured_or_estimated_features_above_the_threshold_are_trusted(
    feature: dict[str, Any] | None, trusted: bool
) -> None:
    assert ft.trusted(feature) is trusted


# --- the exact track map ---------------------------------------------------------------------


@pytest.mark.parametrize("doc", [FULL, MINIMAL])
def test_an_exact_track_is_a_valid_studio_track_map(doc: dict[str, Any]) -> None:
    result = mapped(doc)
    assert valid_track_map(result.track_map, track.TRACK_VERSION)
    m = result.track_map
    assert m["duration"] == 12.0
    assert m["songs"] == [
        {
            "start": 0.0,
            "end": 12.0,
            "bpm": 128.0,
            "first_downbeat": 0.4,
            "first_downbeat_source": "downbeats" if doc is FULL else "first_beat",
        }
    ]
    assert m["events"] == [{"t": 6.0, "kind": "drop", "strength": 0.9, "song": 0}]
    assert result.other_events == ({"t": 4.0, "kind": "build_up", "strength": 0.7},)
    sections = m["sections"]
    assert isinstance(sections, list)
    assert [(s["start"], s["end"]) for s in sections] == [(0.0, 0.4), (0.4, 6.0), (6.0, 12.0)]  # pyright: ignore[reportUnknownVariableType]


def test_section_energy_is_a_rank_within_the_track() -> None:
    sections = mapped(FULL).track_map["sections"]
    assert [s["energy"] for s in sections] == [0.0, 0.5, 1.0]  # pyright: ignore[reportUnknownVariableType, reportGeneralTypeIssues]


def test_flat_energy_has_no_rank() -> None:
    doc = copy.deepcopy(FULL)
    doc["signal"]["energy"]["full"]["values"] = [0.5] * 12
    assert {s["energy"] for s in mapped(doc).track_map["sections"]} == {None}  # pyright: ignore[reportUnknownVariableType, reportGeneralTypeIssues]


@pytest.mark.parametrize("decision", [APPROXIMATE, UNUSABLE])
def test_only_an_exact_track_gets_a_track_map(decision: TimingDecision) -> None:
    assert track_map(FULL, decision, track.TRACK_VERSION) == MapRefused("not_exact")


def _with(doc: dict[str, Any], path: tuple[str, ...], value: Any) -> dict[str, Any]:
    changed = copy.deepcopy(doc)
    node = changed
    for key in path[:-1]:
        node = node[key]
    node[path[-1]] = value
    return changed


@pytest.mark.parametrize(
    ("path", "value", "reason"),
    [
        (("tempo", "method"), "heuristic", "bpm_untrusted"),
        (("tempo", "confidence"), 0.2, "bpm_untrusted"),
        (("tempo", "bpm"), 0, "bpm_untrusted"),
        (("grid", "beats", "method"), "heuristic", "no_beats"),
        (("grid", "beats", "confidence"), 0.1, "no_beats"),
        (("grid", "beats", "times_s"), [], "no_beats"),
        (("signal", "duration_s"), "12", "no_duration"),
    ],
)
def test_a_track_without_a_trusted_tempo_or_grid_keeps_todays_path(
    path: tuple[str, ...], value: Any, reason: str
) -> None:
    assert track_map(_with(FULL, path, value), EXACT, track.TRACK_VERSION) == MapRefused(reason)


def test_a_low_confidence_downbeat_falls_back_to_the_first_beat() -> None:
    doc = _with(FULL, ("grid", "downbeats", "confidence"), 0.2)
    assert mapped(doc).track_map["songs"][0]["first_downbeat_source"] == "first_beat"  # pyright: ignore[reportIndexIssue, reportUnknownMemberType]


def test_heuristic_and_low_confidence_features_drive_nothing() -> None:
    doc = copy.deepcopy(FULL)
    doc["events"][1]["method"] = "heuristic"  # the drop
    doc["structure"]["segments"][1]["boundary_confidence"] = 0.2  # the boundary at 6.0
    result = mapped(doc)
    assert result.track_map["events"] == []
    sections = result.track_map["sections"]
    assert [(s["start"], s["end"]) for s in sections] == [(0.0, 0.4), (0.4, 12.0)]  # pyright: ignore[reportUnknownVariableType, reportGeneralTypeIssues]
    assert Hint("event", 6.0, "drop", 0.8, "heuristic") in result.hints


def test_heuristic_features_reach_the_hints_marked_as_such() -> None:
    assert hints(FULL) == (
        Hint("label", None, "intro", 0.3, "heuristic"),
        Hint("meter", None, "4", 0.7, "heuristic"),
        Hint("phrase", 0.4, None, 0.4, "heuristic"),
    )
    assert hints(MINIMAL) == ()


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (("tempo",), "fast"),
        (("events",), 5),
        (("events",), [1, "x", {"kind": "drop"}]),
        (("signal", "energy", "full", "values"), ["a"]),
        (("signal", "energy"), None),
        (("structure", "segments"), {"s1": 1}),
        (("grid", "downbeats"), {"times_s": "x", "method": "estimated", "confidence": 0.9}),
        (("labels",), [{"label": 7}]),
        (("grid", "meter"), {"beats_per_bar": "x", "method": "heuristic", "confidence": 0.3}),
    ],
)
def test_a_malformed_document_is_refused_or_read_without_raising(
    path: tuple[str, ...], value: Any
) -> None:
    doc = _with(FULL, path, value)
    for result in (
        track_map(doc, EXACT, track.TRACK_VERSION),
        reference_sections(doc, APPROXIMATE),
    ):
        if isinstance(result, MappedTrack):
            assert valid_track_map(result.track_map, track.TRACK_VERSION)
    hints(doc)
    track_facts(doc, EXACT)


# --- the approximate (Spotify) sections ------------------------------------------------------


def sections_of(doc: dict[str, Any]) -> ReferenceSections:
    result = reference_sections(doc, APPROXIMATE)
    assert isinstance(result, ReferenceSections), result
    return result


def test_an_approximate_track_is_mowl_shaped_sections_on_the_reference_clock() -> None:
    result = sections_of(FULL)  # offset -0.4 s, reference 11.9 s
    assert result.bpm == 128.0
    assert [(s["start"], s["duration"]) for s in result.sections] == [(0.0, 5.6), (5.6, 6.0)]
    assert result.events == (
        {"t": 3.6, "kind": "build_up", "window_s": APPROXIMATE_WINDOW_S},
        {"t": 5.6, "kind": "drop", "window_s": APPROXIMATE_WINDOW_S},
    )
    assert all("beats" not in s and "first_downbeat" not in s for s in result.sections)


def test_the_sections_build_the_playlist_map_of_music_data() -> None:
    sections = list(sections_of(FULL).sections)
    tracks = [
        {"Duration": 11900, "BPM": 128, "SpotifyTrackID": SPOTIFY_ID, "Title": "t", "Artist": "a"}
    ]
    m = spotify.build_map(tracks, {SPOTIFY_ID: sections})
    assert [s["start"] for s in m["songs"]] == [0.0, 5.6]
    assert [s["energy"] for s in m["songs"]] == [0.0, 1.0]
    assert [s["loudness"] for s in sections] == [17.4, 19.5]  # mean 0.58 and 0.65, times 30


@pytest.mark.parametrize("decision", [EXACT, UNUSABLE])
def test_only_an_approximate_track_gets_reference_sections(decision: TimingDecision) -> None:
    assert reference_sections(FULL, decision) == MapRefused("not_approximate")


def test_without_an_alignment_there_are_no_reference_sections() -> None:
    assert reference_sections(MINIMAL, APPROXIMATE) == MapRefused("no_alignment")


# --- the facts for track_fit_policy ----------------------------------------------------------


def test_track_facts_come_from_trusted_features() -> None:
    facts = track_facts(FULL, APPROXIMATE)
    assert facts.timing_class == "approximate"
    assert (facts.bpm, facts.duration_s) == (128.0, 12.0)
    assert facts.event_kinds == frozenset({"build_up", "drop"})
    assert facts.low_energy_start is False  # 12 s: the opening is the whole track


def test_a_low_energy_start() -> None:
    doc = copy.deepcopy(FULL)
    doc["signal"]["duration_s"] = 120.0
    doc["signal"]["energy"]["full"]["values"] = [0.2] * 30 + [0.9] * 90
    assert track_facts(doc, EXACT).low_energy_start is True
    doc["signal"]["energy"]["full"]["method"] = "heuristic"
    assert track_facts(doc, EXACT).low_energy_start is None
