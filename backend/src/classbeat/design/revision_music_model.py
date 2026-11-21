"""The ``music`` models of ``class_revisions`` (``design.revision``'s module docstring; split out
of ``design.revision``). Pure: no I/O.

``SwedebeatRevisionMusic`` of a draft designed on spotdown's map of the mix (#1304) also records
``analyser`` (``spotdown``) and that analysis's ``provenance`` record (``mix_map_model``: ids,
versions, the audio identity and the timing class with its evidence; no title, link, URL or
scope); this model does not bound its size: ``mix_map_model.mix_map_of`` refuses a record over
``PROVENANCE_MAX`` before any map is used, as the table's check does on the cache row. A draft on
a local map has neither.
"""

from typing import Annotated, Any, Literal, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


class UnknownBpmTrack(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    index: int = Field(ge=0)
    title: str = Field(max_length=200)


class BeatOneCorrection(BaseModel):
    """One applied beat-one correction (ADR 0007 amendment "beat-one corrections")."""

    model_config = ConfigDict(extra="forbid", strict=True)

    song: int = Field(ge=1, le=500)
    beats: int = Field(ge=-3, le=3)

    @model_validator(mode="after")
    def _not_zero(self) -> Self:
        if self.beats == 0:
            raise ValueError("a correction moves beat 1")
        return self


class RevisionSong(BaseModel):
    """A song the class plays, for the beat-one control (``classbeat.agent.beat_one.songs``):
    seconds of the class as heard; ``title`` for a Spotify track only. Shown to the user only."""

    model_config = ConfigDict(extra="forbid", strict=True)

    song: int = Field(ge=1, le=500)
    start: float = Field(ge=0, le=86400)
    end: float = Field(ge=0, le=86400)
    bpm: float = Field(gt=0, le=1000)
    estimate: bool
    title: str | None = Field(default=None, max_length=100)


# Absent on every revision before the amendment, which therefore validates and dumps as before.
BeatOneList = Annotated[list[BeatOneCorrection] | None, Field(default=None, max_length=500)]
SongList = Annotated[list[RevisionSong] | None, Field(default=None, max_length=200)]


class SpotifyRevisionMusic(BaseModel):
    """``class_revisions.music`` of a draft on a Spotify playlist."""

    model_config = ConfigDict(extra="forbid", strict=True)

    source: Literal["mowl_spotify_playlist"]
    link_id: UUID
    fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    unknown_bpm: list[UnknownBpmTrack] = Field(max_length=500)
    # ADR 0008 section 4: a draft designed on picked songs names the approved ``song_list``
    # revision it was made from; absent on every other draft (stored exactly as before).
    song_list_id: UUID | None = None
    beat_one: BeatOneList = None
    songs: SongList = None


class SwedebeatRevisionMusic(BaseModel):
    """``class_revisions.music`` of a draft on the user's Swedebeat mix (ADR 0016 section 3):
    the mix's opaque id and name, the analysed length and the map's fingerprint; ``link_id`` is
    the user's ``mowl_playlist_links`` row (``source = 'swedebeat'``) Save reads its MOWL playlist
    from, under the same checks as a Spotify link. ``listed_duration`` and ``unsure_beat_one``
    are the library's warnings (the listing's length, the songs whose beat 1 is an estimate)."""

    model_config = ConfigDict(extra="forbid", strict=True)

    source: Literal["mowl_swedebeat_mix"]
    mix_id: str = Field(pattern=r"^[0-9A-Za-z_-]{1,64}$")
    name: str = Field(max_length=200)
    duration: float = Field(gt=0, le=7200)
    listed_duration: float = Field(ge=0, le=86400)
    unsure_beat_one: list[int] = Field(max_length=500)
    fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    link_id: UUID
    beat_one: BeatOneList = None
    songs: SongList = None
    # ADR 0040 section 6 (#1304): set only on a draft designed on spotdown's map of the mix (the
    # analysis kept per class, plan section 4a); absent on a local map, stored exactly as before.
    analyser: Literal["spotdown"] | None = None
    provenance: dict[str, Any] | None = None


class NoRevisionMusic(BaseModel):
    """``class_revisions.music`` of a soundless class's draft (#527): the source and nothing
    else, so no MOWL object, link or fingerprint is named."""

    model_config = ConfigDict(extra="forbid", strict=True)

    source: Literal["none"]
