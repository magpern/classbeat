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
from classbeat.design.kind import DesignDraftInput, SpotifyMusicChoice
from classbeat.jobs.inputs import JobInput
from classbeat.jobs.kinds import JobKind
from classbeat.songs.inputs import DesignWithSongsInput
from classbeat.songs.kind import DESIGN_WITH_SONGS

PLAYLIST: Final = "mowl_spotify_playlist"


def design_input(
    row: StoredInterpretation, job_input: Any, class_id: UUID, *, song_picking: bool
) -> tuple[JobKind, JobInput] | str:
    """The design kind and its unchanged input, or the reason there is none. ``song_picking``:
    the flag admits the user, so a playlist class may get its new playlist (Amendment 26)."""
    if row.music is None:  # a running class has MOWL playlists only; a cycling one needs Spotify
        if row.brief.get("sport") == "running":
            return NEEDS_MOWL_PLAYLIST
        return stored_no_music_reason(row.items)
    if not isinstance(job_input, dict):
        return "not_ready"
    try:
        given = InterpretRequestInput.model_validate(job_input)
        brief = Brief.model_validate(row.brief)
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
