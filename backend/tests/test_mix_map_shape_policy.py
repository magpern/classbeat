"""``design.mix_map_shape_policy.valid_map`` (the check every mix map passes, whoever made it)."""

from typing import Any

from mowl import music

from classbeat.design.mix_map_shape_policy import MAX_EVENTS, MAX_MAP_BYTES, valid_map


def good_map() -> dict[str, Any]:
    return {
        "version": music.MAP_VERSION,
        "duration": 60.0,
        "songs": [{"start": 0.0, "end": 60.0, "bpm": 124.0, "first_downbeat": 0.4}],
        "events": [{"t": 10.0, "kind": "drop"}],
    }


def test_a_map_is_valid_only_with_the_current_version_and_the_fields_the_design_reads() -> None:
    assert valid_map(good_map(), music.MAP_VERSION)
    assert not valid_map(good_map(), music.MAP_VERSION + 1)
    for broken in (
        {**good_map(), "songs": []},
        {**good_map(), "songs": [{"start": 0.0}]},
        {
            **good_map(),
            "songs": [{"start": 0.0, "end": 1.0, "bpm": float("nan"), "first_downbeat": 0}],
        },
        {**good_map(), "events": [{"kind": "drop"}]},
        {**good_map(), "events": "x"},
        {**good_map(), "duration": 0},
        {**good_map(), "duration": True},
        {**good_map(), "events": [{"t": 1.0}] * (MAX_EVENTS + 1)},
        {**good_map(), "pad": "x" * (MAX_MAP_BYTES + 1)},
        [],
        None,
    ):
        assert not valid_map(broken, music.MAP_VERSION)
