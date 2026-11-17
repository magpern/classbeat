"""The music attached to a composer request: ``music_sources`` (ADR 0038 section 3, #1185; pure).

At most ``MAX_SOURCES`` entries ``{kind: "spotify_playlist", id}``, each id a Spotify base-62 id
and each once. **Only Spotify playlists**: a SwedeBeat mix has no readable track list, and a
SwedeBeat playlist needs a gateway path ADR 0036 section 8 does not allow.

Here only the shape and the count are checked: ``interpret_request``'s admit reads the database
only, and a Spotify playlist has no stored listing. The real check is the song fill's (#1188):
every read uses the user's own Spotify connection, and a playlist that token cannot read is Not
as asked ``source_unreadable``. Attached playlists are favoured, never a limit: they are no
``music_override`` (that is "use only this"), the music order of ADR 0029 section 4 rule 4 is
unchanged, and "No music" cannot be combined with them (``check_sources``).
"""

from typing import Annotated, Any, Final, Literal

from pydantic import BaseModel, ConfigDict, Field

from classbeat.design.music_choice import SpotifyPlaylistId

MAX_SOURCES: Final = 5


class MusicSource(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["spotify_playlist"]
    id: SpotifyPlaylistId


MusicSources = Annotated[list[MusicSource], Field(min_length=1, max_length=MAX_SOURCES)]


def check_sources(
    sources: list[MusicSource] | None, override_source: str | None, spotify: Any
) -> None:
    """``ValueError`` (the jobs API's 422 ``invalid_input``) for an id named twice, for "No
    music" with attached music, or for attached music without the Spotify connection."""
    if sources is None:
        return
    if len({s.id for s in sources}) != len(sources):
        raise ValueError("a playlist is attached once")
    if override_source == "none":
        raise ValueError("no music cannot be combined with attached music")
    if spotify is None:
        raise ValueError("attached music needs the Spotify connection")
