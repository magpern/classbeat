"""#1453: a Spotify draft whose map is dropped for its size records why
(``revision_edit_map.fields``, ``edit_map_dropped``), so the editor says ``map_too_large``; any
other missing map names no reason. Pure."""

import math
from typing import Any

import pytest
from pydantic import ValidationError

from classbeat.design import revision_edit_map
from classbeat.design.revision_music_model import SpotifyRevisionMusic

SMALL: dict[str, Any] = {"version": 1, "tracks": [{"sec": 60, "spotify_id": "x", "title": "t"}]}
BIG: dict[str, Any] = {"version": 1, "pad": "x" * revision_edit_map.MAX_BYTES}


def test_a_map_within_the_bound_is_kept_and_names_no_drop() -> None:
    assert revision_edit_map.fields(SMALL, running=False) == {
        "edit_map": {"version": 1, "tracks": [{"sec": 60}]}
    }


def test_a_map_past_the_bound_is_dropped_as_too_large() -> None:
    assert revision_edit_map.fields(BIG, running=False) == {
        "edit_map": None,
        "edit_map_dropped": "map_too_large",
    }


def test_the_bound_is_on_the_map_as_kept_without_song_identity() -> None:
    # Past the bound only with the titles the stored map leaves out: it is kept.
    room = revision_edit_map.MAX_BYTES - 200
    near = {"version": 1, "pad": "x" * room, "tracks": [{"sec": 1, "title": "y" * 400}]}
    assert revision_edit_map.fields(near, running=False)["edit_map"] is not None


@pytest.mark.parametrize(
    ("music_map", "running"),
    [(BIG, True), ({"version": 1, "bpm": math.nan}, False), ({"version": {1}}, False)],
)
def test_a_running_draft_or_a_map_that_is_not_json_names_no_drop(
    music_map: dict[str, Any], running: bool
) -> None:
    found = revision_edit_map.fields(music_map, running=running)
    assert found["edit_map"] is None
    assert found.get("edit_map_dropped") is None


def music(**fields: Any) -> dict[str, Any]:
    return {
        "source": "mowl_spotify_playlist",
        "link_id": "00000000-0000-0000-0000-000000000001",
        "fingerprint": "0" * 64,
        "unknown_bpm": [],
        **fields,
    }


def test_a_kept_map_is_never_also_dropped() -> None:
    kept = SpotifyRevisionMusic.model_validate(music(edit_map=SMALL), strict=False)
    assert kept.edit_map_dropped is None
    dropped = music(edit_map_dropped="map_too_large")
    assert SpotifyRevisionMusic.model_validate(dropped, strict=False).edit_map is None
    with pytest.raises(ValidationError):
        SpotifyRevisionMusic.model_validate(
            music(edit_map=SMALL, edit_map_dropped="map_too_large"), strict=False
        )
