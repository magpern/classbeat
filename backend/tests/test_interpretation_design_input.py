"""The design kind the interpretation hand-off enqueues for each stored music
(``classbeat.classes.interpretation_design.design_input``; ADR 0029 section 5, Amendment 26).
No database, no network, no model."""

import uuid
from typing import Any

from classbeat.classes.interpretation_design import design_input
from classbeat.design import DESIGN_DRAFT
from classbeat.design.interpretation_row import StoredInterpretation
from classbeat.design.kind import DesignDraftInput
from classbeat.songs.inputs import DesignWithSongsInput
from classbeat.songs.kind import DESIGN_WITH_SONGS

CLASS = uuid.uuid4()
MOWL = uuid.uuid4()
SPOTIFY = uuid.uuid4()
PLAYLIST = {"source": "mowl_spotify_playlist", "spotify_playlist_id": "OwnPlaylist00000000001"}
CYCLING = {"length_min": 30, "intensity": "moderate"}
RUNNING = {"sport": "running", "length_min": 15, "intensity": "moderate"}


def planned(
    music: dict[str, str], brief: dict[str, Any], *, spotify: bool, song_picking: bool = True
) -> Any:
    row = StoredInterpretation(job_id=uuid.uuid4(), brief=brief, music=music, items=[])
    given: dict[str, Any] = {"request": "A class, please", "mowl_connection_id": str(MOWL)}
    if spotify:
        given["spotify_connection_id"] = str(SPOTIFY)
    return design_input(row, given, CLASS, song_picking=song_picking)


def test_a_cycling_class_on_the_users_playlist_is_the_pick_pipeline_with_that_source() -> None:
    kind, made = planned(PLAYLIST, CYCLING, spotify=True)
    assert kind is DESIGN_WITH_SONGS
    assert isinstance(made, DesignWithSongsInput)
    assert made.source is not None
    assert made.source.model_dump() == PLAYLIST
    assert (made.mode, made.seed, made.resume) == ("pick", None, None)
    assert (made.class_id, made.mowl_connection_id, made.spotify_connection_id) == (
        CLASS,
        MOWL,
        SPOTIFY,
    )


def test_a_song_pick_names_no_source() -> None:
    kind, made = planned({"source": "pick"}, CYCLING, spotify=True)
    assert kind is DESIGN_WITH_SONGS
    assert isinstance(made, DesignWithSongsInput)
    assert made.source is None
    assert planned({"source": "pick"}, CYCLING, spotify=False) == "not_connected"


def test_without_spotify_or_song_picking_a_running_class_or_a_mix_stays_design_draft() -> None:
    mix = {"source": "mowl_swedebeat_mix", "mix_id": "mix-1"}
    for music, brief, spotify, picking in (
        (PLAYLIST, CYCLING, False, True),  # stored before Amendment 25 only: the old design
        (PLAYLIST, CYCLING, True, False),  # the kill switch of Spotify writes: no new playlist
        (PLAYLIST, RUNNING, True, True),  # ADR 0018: no song picking for running
        (mix, CYCLING, True, True),
        ({"source": "none"}, CYCLING, True, True),
    ):
        kind, made = planned(music, brief, spotify=spotify, song_picking=picking)
        assert kind is DESIGN_DRAFT
        assert isinstance(made, DesignDraftInput)
        assert made.music.model_dump() == music
        # Only a playlist stored with no Spotify (before Amendment 25) skips the connection check.
        assert made.spotify_optional is (True if music == PLAYLIST and not spotify else None)


def test_a_cycling_class_with_no_music_stores_the_reason_its_interpretation_gave() -> None:
    for reason in ("needs_spotify", "song_picking_unavailable"):
        item = {"path": "music", "origin": "not_as_asked", "reason_code": reason, "value": None}
        row = StoredInterpretation(
            job_id=uuid.uuid4(),
            brief=CYCLING,
            music=None,
            items=[item],  # pyright: ignore[reportArgumentType]
        )
        assert design_input(row, {}, CLASS, song_picking=True) == reason
    running = StoredInterpretation(job_id=uuid.uuid4(), brief=RUNNING, music=None, items=[])
    assert design_input(running, {}, CLASS, song_picking=False) == "needs_mowl_playlist"
