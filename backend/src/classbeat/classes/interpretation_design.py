"""The design kind and input of an interpretation's hand-off (split out of
``classbeat.classes.interpretation``). Pure: no database."""

from typing import Any, Final
from uuid import UUID

from pydantic import ValidationError

from classbeat.agent.brief import Brief
from classbeat.design import DESIGN_DRAFT
from classbeat.design.interpret_kind import InterpretRequestInput
from classbeat.design.interpret_music_policy import (
    NEEDS_MOWL_PLAYLIST,
    NEEDS_SPOTIFY,
    stored_no_music_reason,
)
from classbeat.design.interpretation_row import StoredInterpretation
from classbeat.design.kind import DesignDraftInput, NoMusicChoice, SpotifyMusicChoice
from classbeat.jobs.inputs import JobInput
from classbeat.jobs.kinds import JobKind
from classbeat.songs.inputs import DesignWithSongsInput
from classbeat.songs.kind import DESIGN_WITH_SONGS

PLAYLIST: Final = "mowl_spotify_playlist"
NO_MUSIC: Final = "none"


def _undecided(row: StoredInterpretation) -> bool:
    return row.music_decision is not None and row.music_decision.state == "undecided"


def _sketched(row: StoredInterpretation, brief: Brief) -> bool:
    """A composer class that gets a sketch: cycling, with music (not "No music"), or with its
    music left open (``undecided``) even when the reading found no music it could use (song
    picking was off for the user): the sketch needs none (ADR 0038 amendment of 2026-11-24,
    decisions 5 and 6). With no Spotify a composer reading takes a song pick or the named
    playlist (``Offer.composed``), so that class has music."""
    if brief.sport != "cycling":
        return False
    source = (row.music or {}).get("source")
    return source not in (None, NO_MUSIC) or (source is None and _undecided(row))


def _no_music(row: StoredInterpretation) -> str:
    """Why a class with no music has no design: a running class has MOWL playlists only; a
    cycling one needs Spotify (or song picking)."""
    if row.brief.get("sport") == "running":
        return NEEDS_MOWL_PLAYLIST
    return stored_no_music_reason(row.items)


def design_input(
    row: StoredInterpretation,
    job_input: Any,
    class_id: UUID,
    *,
    song_picking: bool,
    composer: bool = False,
) -> tuple[JobKind, JobInput] | str:
    """The design kind and its unchanged input, or the reason there is none. ``song_picking``:
    the flag admits the user, so a playlist class may get its new playlist (Amendment 26).
    ``composer``: the composer is available now; a cycling class whose interpret job was
    admitted with it and that has music, or whose music is undecided, gets a sketch (ADR 0038
    section 4), a ``design_draft`` with no music in mode ``sketch``. A soundless class keeps its
    soundless design, and a running class its design on a MOWL playlist (the sketch's stage is the
    soundless one, cycling only). A class with no music that takes the older route (the composer
    is not available now) is refused as before, and so is one read for the composer with no
    Spotify (a song pick or a Spotify playlist, ``Offer.composed``): ``needs_spotify``
    (Amendment 25)."""
    if row.music is None and not (composer and _undecided(row)):
        return _no_music(row)
    if not isinstance(job_input, dict):
        return "not_ready"
    try:
        given = InterpretRequestInput.model_validate(job_input)
        brief = Brief.model_validate(row.brief)
        if composer and given.composer is True and _sketched(row, brief):
            return DESIGN_DRAFT, DesignDraftInput(
                class_id=class_id,
                brief=brief,
                music=NoMusicChoice(source=NO_MUSIC),
                mowl_connection_id=given.mowl_connection_id,
                mode="sketch",
            )
        if row.music is None:  # undecided, but not a sketch: the older route needs the music
            return _no_music(row)
        pick = row.music.get("source") == "pick"
        # Amendment 26: a cycling class on the user's playlist gets a new one in class order. One
        # stored with no Spotify (only before Amendment 25) keeps its write-free design_draft.
        playlist = row.music.get("source") == PLAYLIST
        own_playlist = playlist and brief.sport == "cycling"
        own = own_playlist and song_picking
        if (
            given.composer is True
            and given.spotify_connection_id is None
            and (pick or own_playlist)
        ):
            # Read for the composer, which needs no Spotify until the song search; the older
            # route needs it now (the addendum's fallback): Amendment 25's refusal.
            return NEEDS_SPOTIFY
        if pick or (own and given.spotify_connection_id is not None):
            if given.spotify_connection_id is None:
                return "not_connected"
            songs = DesignWithSongsInput(
                class_id=class_id,
                brief=brief,
                source=None if pick else SpotifyMusicChoice.model_validate(row.music),
                mowl_connection_id=given.mowl_connection_id,
                spotify_connection_id=given.spotify_connection_id,
            )
            return DESIGN_WITH_SONGS, songs
        draft = DesignDraftInput.model_validate(
            {
                "class_id": class_id,
                "brief": brief,
                "music": row.music,
                "mowl_connection_id": given.mowl_connection_id,
                # Stored before Amendment 25 with no Spotify: keeps its write-free design.
                "spotify_optional": (
                    True if own_playlist and given.spotify_connection_id is None else None
                ),
            }
        )
        return DESIGN_DRAFT, draft
    except ValidationError:
        return "invalid_input"
