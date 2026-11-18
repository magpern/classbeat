"""The design kind and input of an interpretation's hand-off (split out of
``classbeat.classes.interpretation``). Pure: no database."""

from typing import Any, Final
from uuid import UUID

from pydantic import ValidationError

from classbeat.agent.brief import Brief
from classbeat.design import DESIGN_DRAFT
from classbeat.design.interpret_kind import InterpretRequestInput
from classbeat.design.interpret_music_policy import NEEDS_MOWL_PLAYLIST, stored_no_music_reason
from classbeat.design.interpretation_row import StoredInterpretation
from classbeat.design.kind import DesignDraftInput, NoMusicChoice, SpotifyMusicChoice
from classbeat.jobs.inputs import JobInput
from classbeat.jobs.kinds import JobKind
from classbeat.songs.inputs import DesignWithSongsInput
from classbeat.songs.kind import DESIGN_WITH_SONGS

PLAYLIST: Final = "mowl_spotify_playlist"
NO_MUSIC: Final = "none"


def _sketched(row: StoredInterpretation, brief: Brief) -> bool:
    """A composer class that gets a sketch: cycling, with music (not "No music")."""
    music = row.music or {}
    return brief.sport == "cycling" and music.get("source") not in (None, NO_MUSIC)


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
    ``composer``: the ``composer_flow`` flag is on now; a cycling class whose interpret job was
    admitted with it on and that has music gets a sketch (ADR 0038 section 4), a ``design_draft``
    with no music in mode ``sketch``. A soundless class keeps its soundless design, and a running
    class its design on a MOWL playlist (the sketch's stage is the soundless one, cycling only)."""
    if row.music is None:  # a running class has MOWL playlists only; a cycling one needs Spotify
        if row.brief.get("sport") == "running":
            return NEEDS_MOWL_PLAYLIST
        return stored_no_music_reason(row.items)
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
        pick = row.music.get("source") == "pick"
        # Amendment 26: a cycling class on the user's playlist gets a new one in class order. One
        # stored with no Spotify (only before Amendment 25) keeps its write-free design_draft.
        playlist = row.music.get("source") == PLAYLIST
        own_playlist = playlist and brief.sport == "cycling"
        own = own_playlist and song_picking
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
