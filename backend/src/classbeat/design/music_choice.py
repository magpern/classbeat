"""The music a design job or a class names (split out of ``classbeat.design.kind``, which
re-exports every name): a Spotify playlist, the user's Swedebeat mix (ADR 0016 section 3), or no
music (ADR 0007 amendment of 2026-11-03, #527), and the shapes of ``classes.music_source``."""

from typing import Annotated, Final, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, TypeAdapter

SpotifyPlaylistId = Annotated[str, StringConstraints(pattern=r"^[0-9A-Za-z]{22}$")]
# The opaque ``ID`` of MOWL's Swedebeat listing (the ``mowl_playlist_links`` check).
MixId = Annotated[str, StringConstraints(pattern=r"^[0-9A-Za-z_-]{1,64}$")]


class SpotifyMusicChoice(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source: Literal["mowl_spotify_playlist"]
    spotify_playlist_id: SpotifyPlaylistId


class SwedebeatMusicChoice(BaseModel):
    """The user's Swedebeat mix (ADR 0016 section 3): ``admit`` checks it is in their own
    listing (``classbeat.design.mixadmit``)."""

    model_config = ConfigDict(extra="forbid")

    source: Literal["mowl_swedebeat_mix"]
    mix_id: MixId


class NoMusicChoice(BaseModel):
    """A cycling class with no music (ADR 0007 amendment of 2026-11-03, item 1; #527). Also the
    class's stored ``music_source`` (``{"source": "none"}``)."""

    model_config = ConfigDict(extra="forbid")

    source: Literal["none"]


MusicChoice = Annotated[
    SpotifyMusicChoice | SwedebeatMusicChoice | NoMusicChoice, Field(discriminator="source")
]


class ClassSpotifyMusic(SpotifyMusicChoice):
    """``classes.music_source`` of a class on a Spotify playlist, and for a class designed on
    picked songs (ADR 0008 section 4) ``origin = "song_pick"`` and its ``class_playlists`` row.
    Never a job input: ``design_draft`` takes ``MusicChoice``, which forbids both fields."""

    origin: Literal["song_pick"] | None = None
    class_playlist_id: UUID | None = None


ClassMusicSource = Annotated[
    ClassSpotifyMusic | SwedebeatMusicChoice | NoMusicChoice, Field(discriminator="source")
]
CLASS_MUSIC_SOURCE: Final = TypeAdapter[ClassSpotifyMusic | SwedebeatMusicChoice | NoMusicChoice](
    ClassMusicSource
)
