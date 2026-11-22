"""#1424 pure parts (no database, no network): the edit map a Spotify draft keeps
(``design.revision_edit_map``, its bound on ``SpotifyRevisionMusic``) and the edit's state
(``classes.spotify_edit_state``): which edits a Spotify draft refuses, and the length the music
allows."""

import math
import uuid
from typing import Any

import pytest
from pydantic import ValidationError

from classbeat.classes import spotify_edit_state as spot
from classbeat.classes.spotify_edit_draft import SpotifyDraft
from classbeat.classes.user_edit_model import (
    AnchorBlock,
    ClearAll,
    Drag,
    Duration,
    LockBlock,
    PinClear,
    PinConfirm,
    PinSet,
    RemoveAnchor,
    SetAttribute,
    Unlock,
    UseAiValue,
)
from classbeat.design import revision_edit_map as emap
from classbeat.design.edit_overlay_model import EMPTY
from classbeat.design.revision import SpotifyRevisionMusic

MAP: dict[str, Any] = {
    "version": 13,
    "duration": 931.38,
    "crossfade": 9.54,
    "songs": [],
    "tracks": [],
}


def music(**more: Any) -> SpotifyRevisionMusic:
    return SpotifyRevisionMusic.model_validate(
        {
            "source": "mowl_spotify_playlist",
            "link_id": uuid.uuid4(),
            "fingerprint": "a" * 64,
            "unknown_bpm": [],
            **more,
        },
        strict=False,
    )


def draft(edit_map: dict[str, Any], **more: Any) -> SpotifyDraft:
    return SpotifyDraft(
        uuid.uuid4(), b"\x00" * 32, {"segments": []}, music(**more), EMPTY, edit_map
    )


def test_a_map_is_kept_as_a_plain_copy() -> None:
    kept = emap.stored(MAP)
    assert kept == MAP
    assert kept is not MAP


@pytest.mark.parametrize("bad", [math.nan, math.inf, object()])
def test_a_map_that_is_not_finite_json_is_not_kept(bad: object) -> None:
    assert emap.stored({**MAP, "duration": bad}) is None


def test_a_map_past_the_bound_is_not_kept_and_the_bound_holds_on_the_model() -> None:
    big = {**MAP, "events": ["x" * 1000] * (emap.MAX_BYTES // 1000 + 1)}
    assert emap.stored(big) is None
    with pytest.raises(ValidationError, match="too large"):
        music(edit_map=big)
    assert music(edit_map=MAP).edit_map == MAP
    assert music().edit_map is None


@pytest.mark.parametrize(
    "edit",
    [
        PinSet(op="pin_set", piece=0, edge="start", requested_sec=10.0, mode="exact"),
        PinConfirm(op="pin_confirm", piece=0, edge="start"),
        PinClear(op="pin_clear", piece=0, edge="end"),
        AnchorBlock(
            op="anchor",
            block=1,
            edge="start",
            event={"piece": 0, "source_ms": 1000, "kind": "drop"},  # pyright: ignore[reportArgumentType]
        ),
        RemoveAnchor(op="remove_anchor", block=1, edge="start"),
    ],
)
def test_an_edit_that_needs_a_studio_piece_is_refused(edit: Any) -> None:
    assert spot.refusal(edit) == "audio_edit_unavailable"


@pytest.mark.parametrize(
    "edit",
    [
        Drag(op="drag", block=0, delta_sec=5),
        Duration(op="duration", total_sec=600),
        LockBlock(op="lock", block=0, scope="all"),
        Unlock(op="unlock", block=0),
        SetAttribute(op="set", block=0, field="zone", value=3),
        UseAiValue(op="use_ai_value", block=0, field="zone"),
        ClearAll(op="clear_all"),
    ],
)
def test_a_workout_or_overlay_edit_is_allowed(edit: Any) -> None:
    assert spot.refusal(edit) is None


def test_the_state_is_the_draft_on_its_own_map_with_no_studio_parts() -> None:
    state = spot.state_of(draft(MAP))
    assert state is not None
    assert state.fixed_map == MAP
    assert state.map_version == 13
    assert state.max_total_sec == 931  # no songs recorded: the map's length
    assert (state.music.references, state.analysed, state.choices) == ((), {}, None)
    assert state.spec == {"tracks": []}


def test_the_class_may_not_run_past_the_last_song_it_plays() -> None:
    songs = [
        {"song": 1, "start": 0.0, "end": 400.0, "bpm": 120.0, "estimate": False},
        {"song": 2, "start": 400.0, "end": 820.5, "bpm": 124.0, "estimate": False},
    ]
    state = spot.state_of(draft(MAP, songs=songs))
    assert state is not None
    assert state.max_total_sec == 820


@pytest.mark.parametrize(
    "broken",
    [
        {k: v for k, v in MAP.items() if k != "version"},
        {**MAP, "version": "13"},
        {**MAP, "version": True},
        {**MAP, "version": 0},
        {k: v for k, v in MAP.items() if k != "duration"},
        {**MAP, "duration": math.nan},
        {**MAP, "duration": "931"},
    ],
)
def test_a_map_the_library_could_not_have_made_has_no_state(broken: dict[str, Any]) -> None:
    assert spot.state_of(draft(broken)) is None
