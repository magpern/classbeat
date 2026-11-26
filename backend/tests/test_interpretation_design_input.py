"""The design kind the interpretation hand-off enqueues for each stored music
(``classbeat.classes.interpretation_design.design_input``; ADR 0029 section 5, Amendment 26).
No database, no network, no model."""

import uuid
from typing import Any

import pytest

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
TASTE = {"eras": [], "genres": [], "hits": True, "language": None}
HITS = {**CYCLING, "music_taste": TASTE}


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
    """With a hint of what to play (a taste, #1536: no song is picked at random)."""
    kind, made = planned({"source": "pick"}, HITS, spotify=True)
    assert kind is DESIGN_WITH_SONGS
    assert isinstance(made, DesignWithSongsInput)
    assert made.source is None
    assert planned({"source": "pick"}, HITS, spotify=False) == "not_connected"


@pytest.mark.parametrize("spotify", [True, False])
def test_a_song_pick_with_no_hint_is_refused_music_unanswered(spotify: bool) -> None:
    assert planned({"source": "pick"}, CYCLING, spotify=spotify) == "music_unanswered"


@pytest.mark.parametrize(
    ("override", "sources", "expected"),
    [
        ({"source": "pick"}, None, "music_unanswered"),  # "Pick songs for me" is no hint
        (None, [{"kind": "spotify_playlist", "id": "0123456789abcdefABCDEF"}], "designed"),
        (None, None, "music_unanswered"),
    ],
    ids=["form pick", "music_sources", "nothing"],
)
def test_what_the_form_gave_is_a_hint_unless_it_is_a_pick(
    override: dict[str, str] | None, sources: Any, expected: str
) -> None:
    """A class read with the composer handed off on the older route (Spotify connected): the
    route that starts the pick now."""
    row = composer_row({"state": "decided", "choice": "request"}, {"source": "pick"})
    given = composer_given(spotify=True)
    if override is not None:
        given["music_override"] = override
    if sources is not None:
        given["music_sources"] = sources
    got = design_input(row, given, CLASS, song_picking=True, composer=False)
    assert (got if isinstance(got, str) else "designed") == expected
    if expected == "designed":
        assert got[0] is DESIGN_WITH_SONGS  # pyright: ignore[reportIndexIssue]


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


# --- the music decision (ADR 0038 amendment of 2026-11-24, decisions 5 and 6, #1495) ---------

NEEDS_SPOTIFY = {
    "path": "music",
    "origin": "not_as_asked",
    "reason_code": "needs_spotify",
    "value": None,
}


def composer_row(decision: dict[str, Any] | None, music: dict[str, str] | None = None) -> Any:
    return StoredInterpretation.model_validate(
        {
            "job_id": str(uuid.uuid4()),
            "brief": CYCLING,
            "music": music,
            "items": [] if music else [NEEDS_SPOTIFY],
            "music_decision": decision,
        }
    )


def composer_given(*, spotify: bool = False) -> dict[str, Any]:
    given: dict[str, Any] = {
        "request": "A class, please",
        "mowl_connection_id": str(MOWL),
        "composer": True,
    }
    if spotify:
        given["spotify_connection_id"] = str(SPOTIFY)
    return given


def test_a_composer_class_with_its_music_undecided_is_sketched_without_spotify() -> None:
    row = composer_row({"state": "undecided"})
    kind, made = design_input(row, composer_given(), CLASS, song_picking=True, composer=True)
    assert kind is DESIGN_DRAFT
    assert isinstance(made, DesignDraftInput)
    assert (made.mode, made.music.model_dump()) == ("sketch", {"source": "none"})


def test_without_the_composer_now_an_undecided_class_is_refused_needs_spotify() -> None:
    """The addendum's fallback: the composer was available at admit, not at hand-off; the older
    route needs Spotify, so the stored refusal is ``needs_spotify`` (no credit)."""
    row = composer_row({"state": "undecided"})
    got = design_input(row, composer_given(), CLASS, song_picking=True, composer=False)
    assert got == "needs_spotify"


def test_a_decided_class_with_no_music_it_could_use_keeps_amendment_25() -> None:
    """No music, yet decided as requested: only when song picking was off for the user at the
    reading (a taste needs a song pick; a disconnected instructor's reading takes the pick,
    ``Offer.composed``), so the hand-off refuses as before."""
    for decision in ({"state": "decided", "choice": "request"}, None):
        row = composer_row(decision)
        got = design_input(row, composer_given(), CLASS, song_picking=True, composer=True)
        assert got == "needs_spotify"


@pytest.mark.parametrize("music", [{"source": "pick"}, PLAYLIST])
def test_a_class_read_for_the_composer_without_spotify_needs_it_on_the_older_route(
    music: dict[str, str],
) -> None:
    """The addendum's fallback for a reading that took a pick or a playlist with no Spotify
    (``Offer.composed``): ``needs_spotify``, never a write-free design or ``not_connected``."""
    for decision in ({"state": "undecided"}, {"state": "decided", "choice": "request"}):
        row = composer_row(decision, music)
        got = design_input(row, composer_given(), CLASS, song_picking=True, composer=False)
        assert got == "needs_spotify"
        kind, made = design_input(row, composer_given(), CLASS, song_picking=True, composer=True)
        assert kind is DESIGN_DRAFT
        assert isinstance(made, DesignDraftInput)
        assert made.mode == "sketch"


def test_a_composer_class_with_music_is_sketched_whatever_its_decision() -> None:
    for decision in ({"state": "undecided"}, {"state": "decided", "choice": "request"}, None):
        row = composer_row(decision, {"source": "pick"})
        given = composer_given(spotify=True)
        kind, made = design_input(row, given, CLASS, song_picking=True, composer=True)
        assert kind is DESIGN_DRAFT
        assert isinstance(made, DesignDraftInput)
        assert made.mode == "sketch"
