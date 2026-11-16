"""SR C6a, the pure parts (ADR 0036 sections 4.3, 5, 6 and 10): the output choice's rules
(``studio.output_choice_policy``) and the music timeline (``studio.timeline_calc``) against
``mowl.compose.compose_map`` itself. No database, no network."""

from typing import Any

import pytest
from mowl import compose

from classbeat.studio.output_choice_policy import (
    OUTPUTS,
    canonical,
    enabled_outputs,
    needs_render,
    refusal,
)
from classbeat.studio.timeline_calc import SongFacts, timeline
from studiomaps import track_map

ALL = frozenset(OUTPUTS)


@pytest.mark.parametrize(
    ("chosen", "enabled", "reason"),
    [
        (["swedebeat_playlist"], ALL, None),  # the playlist needs no render and no MOWL
        (["swedebeat_mix"], ALL, None),  # the mix does not need the playlist
        (["swedebeat_mix", "mowl"], ALL, None),
        (["spotify_playlist"], frozenset({"spotify_playlist"}), None),
        (list(OUTPUTS), ALL, None),
        (["mowl"], ALL, "mowl_needs_mix"),
        (["swedebeat_playlist", "mowl"], ALL, "mowl_needs_mix"),
        (["swedebeat_playlist"], ALL - {"swedebeat_playlist"}, "output_off"),
        (["swedebeat_mix", "mowl"], ALL - {"mowl"}, "output_off"),
        (["spotify_playlist"], ALL - {"spotify_playlist"}, "output_off"),
        ([], ALL, "invalid_input"),
        (["swedebeat_mix", "swedebeat_mix"], ALL, "invalid_input"),
        (["private_playlist"], ALL, "invalid_input"),  # there is no private playlist output
        (["classbeat_player"], ALL, "invalid_input"),  # later (section 9), not here
    ],
)
def test_the_choice_rules(chosen: list[str], enabled: frozenset[str], reason: str | None) -> None:
    assert refusal(chosen, enabled) == reason


def test_the_dependency_is_checked_before_the_setting() -> None:
    assert refusal(["mowl"], frozenset()) == "mowl_needs_mix"


def test_a_choice_is_kept_in_one_order() -> None:
    assert canonical(["mowl", "swedebeat_mix", "spotify_playlist"]) == (
        "spotify_playlist",
        "swedebeat_mix",
        "mowl",
    )
    with pytest.raises(ValueError, match="once"):
        canonical(["mowl", "mowl"])


def test_only_the_mix_needs_a_render() -> None:
    assert needs_render(["swedebeat_mix", "mowl"])
    assert not needs_render(["swedebeat_playlist", "spotify_playlist"])


def test_the_enabled_outputs_follow_the_setting_and_spotify() -> None:
    studio = frozenset({"design", "swedebeat_playlist", "swedebeat_mix", "mowl"})
    assert enabled_outputs(studio, spotify_on=False) == {
        "swedebeat_playlist",
        "swedebeat_mix",
        "mowl",
    }
    assert enabled_outputs({"design"}, spotify_on=True) == {"spotify_playlist"}
    assert enabled_outputs({"design"}, spotify_on=False) == frozenset()


# --- The timeline ------------------------------------------------------------------------------

SPEC: dict[str, Any] = {
    "tracks": [
        {"reference": "ref-b", "start_sec": 30.4, "end_sec": 250.0},
        {"reference": "ref-a", "start_sec": 0, "end_sec": 300.0},
        {"reference": "ref-c", "start_sec": 15.4, "end_sec": 200.0},
    ],
    "crossfades": [8, 12],
}
MAPS = [track_map(260.0), track_map(300.0), track_map(220.0, bpm=120.0)]
FACTS = [SongFacts(f"Invented {n}", "Made-up Band", 128.0) for n in "bac"]
CONTENT: dict[str, Any] = {
    "name": "Studio climb",
    "segments": [
        {"name": "Warm up", "kind": "warmup", "intervals": [
            {"sec": 300, "zone": 2, "rpm": [85, 95], "standing": False, "cue": "easy"},
        ]},
        {"name": "Climb", "kind": "main", "intervals": [
            {"sec": 240, "zone": 4, "rpm": [65, 75], "standing": True},
            {"sec": 120, "zone": 1, "rpm": "beat", "standing": False, "game": "racetrack"},
        ]},
    ],
}  # fmt: skip


def test_the_timeline_is_the_composed_map() -> None:
    made = timeline(SPEC, MAPS, FACTS, CONTENT)
    composed = compose.compose_map([dict(m) for m in MAPS], SPEC)
    assert made["duration_sec"] == composed["duration"]
    windows = [t["end_sec"] - t["start_sec"] for t in SPEC["tracks"]]
    assert made["duration_sec"] == pytest.approx(sum(windows) - sum(SPEC["crossfades"]))
    for track, song in zip(made["tracks"], composed["songs"], strict=True):
        assert (track["mix_start_sec"], track["mix_end_sec"]) == (song["start"], song["end"])
        assert track["first_downbeat_sec"] == song["first_downbeat"]
    # One duration rule: the effective lengths (until the next track starts) add up to it.
    assert sum(t["effective_sec"] for t in made["tracks"]) == pytest.approx(made["duration_sec"])
    assert [t["played_sec"] for t in made["tracks"]] == [219.6, 300.0, 184.6]
    assert [(t["crossfade_in_sec"], t["crossfade_out_sec"]) for t in made["tracks"]] == [
        (0, 8),
        (8, 12),
        (12, 0),
    ]
    assert [t["title"] for t in made["tracks"]] == ["Invented b", "Invented a", "Invented c"]
    fades = made["crossfades"]
    assert [f["sec"] for f in fades] == SPEC["crossfades"]
    pairs = zip(made["tracks"], made["tracks"][1:], strict=False)
    for fade, (left, right) in zip(fades, pairs, strict=True):
        assert fade["mix_from_sec"] == right["mix_start_sec"]
        assert fade["mix_to_sec"] == left["mix_end_sec"]
        assert fade["mix_to_sec"] - fade["mix_from_sec"] == pytest.approx(fade["sec"])


def test_the_intervals_lie_on_the_mix() -> None:
    made = timeline(SPEC, MAPS, FACTS, CONTENT)
    spans = [(i["start_sec"], i["end_sec"], i["track"]) for i in made["intervals"]]
    # The second track starts at 211.6 s, the third at 499.6 s.
    assert spans == [(0, 300, 0), (300, 540, 1), (540, 660, 2)]
    assert made["workout_sec"] == 660
    assert made["intervals"][2]["game"] == "racetrack"
    assert "cue" not in made["intervals"][0]  # the timeline carries no cue text


def test_no_reference_is_in_the_timeline() -> None:
    made = repr(timeline(SPEC, MAPS, FACTS, CONTENT))
    assert "ref-" not in made


def test_maps_that_do_not_fit_the_cuts_are_refused() -> None:
    with pytest.raises(ValueError, match="after the track"):
        timeline(SPEC, [track_map(100.0), *MAPS[1:]], FACTS, CONTENT)
    with pytest.raises(ValueError, match="one set of facts"):
        timeline(SPEC, MAPS, FACTS[:2], CONTENT)
