"""The ``song_list`` revision's content and music models (ADR 0008 section 5, ADR 0038 section 5),
split out of ``classbeat.songs.song_list`` (CLAUDE.md, split on touch): the content of ``v: 1`` to
``v: 3``, the music, the hash and the fingerprint. ``classbeat.songs.song_list`` re-exports them
and documents the fields. Pure: no database."""

import hashlib
import json
from typing import Annotated, Any, Final, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from classbeat.agent.interpretation_record import InterpretationItem
from classbeat.agent.schemas import MAX_SONGS, Role
from classbeat.covers import cover_url
from classbeat.design.revision import canonical
from classbeat.playlists import common
from classbeat.songs.preview_model import Preview

VERSION: Final = 2
PREVIEW_VERSION: Final = 3  # a composer's list, with its ``preview``
MUSIC_SOURCE: Final = "claude_pick"
MAX_TEXT: Final = 200
MAX_NOTE: Final = 300
MAX_UNMET: Final = 8

SpotifyId = Annotated[str, Field(pattern=r"^[0-9A-Za-z]{22}$")]


class SongSource(BaseModel):
    """Where a composer's song came from (``v: 3``, ADR 0038 section 10.2): ``pinned`` by the user,
    one of the attached ``playlist``s (``playlist``: its position in the request's
    ``music_sources``; the page names it), or ``search``."""

    model_config = ConfigDict(extra="forbid", strict=True)

    kind: Literal["pinned", "playlist", "search"]
    playlist: int | None = Field(default=None, ge=0, le=4, exclude_if=lambda v: v is None)

    @model_validator(mode="after")
    def _playlist_named(self) -> "SongSource":
        if (self.kind == "playlist") != (self.playlist is not None):
            raise ValueError("a playlist song names its playlist, and only it")
        return self


class Song(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    spotify_id: SpotifyId
    title: str = Field(max_length=MAX_TEXT)
    artist: str = Field(max_length=MAX_TEXT)
    role: Role
    bpm: float = Field(gt=0, le=400)
    length_sec: int = Field(ge=1, le=7200)
    new: bool
    cover: str | None = None  # v: 2 (ADR 0013 section 9); absent in v: 1
    source: SongSource | None = Field(default=None, exclude_if=lambda v: v is None)  # v: 3

    @field_validator("cover", mode="before")
    @classmethod
    def _pinned_host(cls, value: object) -> str | None:
        """Anything but an album cover URL on the pinned host is ``None``, never an error."""
        return cover_url(value)


class SongListContent(BaseModel):
    """``class_revisions.content`` of a ``song_list``."""

    model_config = ConfigDict(extra="forbid", strict=True)

    v: Literal[1, 2, 3]
    mode: Literal["pick", "replace"]
    target_sec: int = Field(ge=1)
    total_sec: int = Field(ge=0)
    crossfade_sec: float = Field(ge=0, le=12)
    songs: list[Song] = Field(min_length=1, max_length=MAX_SONGS)
    omitted_no_bpm: int = Field(ge=0)
    note: str = Field(max_length=MAX_NOTE)
    # Left out of the dump when empty, so a list that keeps to the request hashes as before.
    unmet: list[InterpretationItem] = Field(
        default_factory=lambda: [], max_length=MAX_UNMET, exclude_if=lambda v: not v
    )
    preview: Preview | None = Field(default=None, exclude_if=lambda v: v is None)  # v: 3 only

    @model_validator(mode="after")
    def _cover_is_v2(self) -> "SongListContent":
        if self.v == 1 and any("cover" in s.model_fields_set for s in self.songs):
            raise ValueError("cover is a v: 2 field")
        if any(i.origin != "not_as_asked" for i in self.unmet):
            raise ValueError("unmet holds Not as asked items only")
        if self.v != PREVIEW_VERSION and any(s.source is not None for s in self.songs):
            raise ValueError("a song's source is a v: 3 field")
        if (self.v == PREVIEW_VERSION) != (self.preview is not None):
            raise ValueError("a preview is in v: 3 and only there")
        if self.preview is not None and (
            self.preview.music.fingerprint != fingerprint(self.track_ids())
            or len(self.preview.music.songs) != len(self.songs)
        ):
            raise ValueError("the preview is of these songs, one span each")
        return self

    def track_ids(self) -> tuple[str, ...]:
        return tuple(s.spotify_id for s in self.songs)


class Generations(BaseModel):
    """The ``generation`` of each connection the list was made with (ADR 0009 section 5.4)."""

    model_config = ConfigDict(extra="forbid", strict=True)

    mowl: int = Field(ge=0)
    spotify: int = Field(ge=0)


class SongListMusic(BaseModel):
    """``class_revisions.music`` of a ``song_list``."""

    model_config = ConfigDict(extra="forbid", strict=True)

    source: Literal["claude_pick"]
    fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    spotify_user_id: str = Field(min_length=1, max_length=100)
    # The connections phase 1 used; the approved phase 2 refuses (``not_connected``) when either
    # was reconnected since. A resume uses the connections as they are now (section 6).
    generations: Generations | None = None


def content_hash(content: dict[str, Any]) -> bytes:
    return hashlib.sha256(canonical(content)).digest()


def fingerprint(track_ids: tuple[str, ...]) -> str:
    """The ordered ids' SHA-256 (hex): what the final playlist must hold, in this order."""
    return common.ids_hash(common.check_track_ids(track_ids)).hex()


def stored_bytes(content: dict[str, Any]) -> int:
    """The bytes migration 0004's check measures, ``octet_length(content::text)``: Postgres prints
    ``jsonb`` with ``", "`` and ``": "`` between items, so a list can be larger there than its
    canonical JSON. ``build`` checks both before the insert (ADR 0038 section 5)."""
    return len(json.dumps(content, ensure_ascii=False, separators=(", ", ": ")).encode("utf-8"))
