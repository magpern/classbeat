"""The ``preview`` of a composer's song list (ADR 0038 section 5, #1188). Pure: no database.

A ``song_list`` of a ``fill``, ``swap`` or ``change`` (content ``v: 3``) carries the class designed
on its songs before the approval, **inside** the immutable list, so the approval's hash covers the
songs and the design, and nothing before the approval is a revision that could be saved:

    "preview": {"content": {<the draft's content>},
                "music": {"fingerprint": "<hex sha-256 of the ordered track ids>",
                          "songs": [{"start": 0.0, "end": 241.0}, ...]},
                "design_run_id": "<the design stage's agent_runs id>"}

- ``content`` is the design stage's draft content as a draft revision stores it (it parses as one,
  ``agent.stored_draft``); it is never a revision, never current, never saved.
- ``music`` is **small**: only what phase 2's fit compares (ADR 0038 section 7), never the pool's
  analysis. ``fingerprint`` is the ordered track ids' (``songs.song_list.fingerprint``, the same
  value as the list's ``music.fingerprint``); ``songs`` is each song's heard ``start`` and ``end``
  in class seconds, in play order, built by ``song_times`` from an analysis of exactly these
  songs in this order, the function the fit uses on the final playlist's analysis (like with
  like).
- ``design_run_id`` names the ``agent_runs`` row of the run that designed it (provenance).

The whole list's canonical content stays at or under 64 KB (``song_list.build`` checks it before
anything is inserted; migration 0004's check is not raised).
"""

from collections.abc import Mapping
from typing import Any, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from classbeat.agent.schemas import MAX_SONGS
from classbeat.agent.stored_draft import stored_draft
from classbeat.crossfade_calc import track_shift_sec

MAX_CLASS_SECONDS = 4 * 3600.0


class PreviewSong(BaseModel):
    """One song's heard span in the class: from its first section's start to its last's end."""

    model_config = ConfigDict(extra="forbid", strict=True)

    start: float = Field(ge=0, le=MAX_CLASS_SECONDS)
    end: float = Field(gt=0, le=MAX_CLASS_SECONDS)

    @model_validator(mode="after")
    def _ordered(self) -> Self:
        if self.end <= self.start:
            raise ValueError("a song ends after it starts")
        return self


class PreviewMusic(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    songs: list[PreviewSong] = Field(min_length=1, max_length=MAX_SONGS)


class Preview(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    content: dict[str, Any]
    music: PreviewMusic
    design_run_id: UUID

    @field_validator("design_run_id", mode="before")
    @classmethod
    def _uuid_text(cls, value: object) -> object:
        """Stored as JSON text; strict mode would refuse the string form otherwise."""
        return UUID(value) if isinstance(value, str) else value

    @field_validator("content")
    @classmethod
    def _a_draft(cls, value: dict[str, Any]) -> dict[str, Any]:
        """The design's content parses as a cycling draft's (a sketch's songs are cycling)."""
        stored_draft(value, "cycling")
        return value


def song_times(analysis_map: Mapping[str, Any]) -> list[PreviewSong]:
    """Each track's heard span, in play order, from an analysis map (``mowl.spotify.build_map``):
    the earliest ``start`` and the latest ``end`` of the sections of that track, in class
    seconds (the crossfade is in them: classbeat's, ``crossfade_calc.CROSSFADE_SEC``, whatever the
    stored map was built with). ``ValueError`` for a track with no section."""
    tracks = analysis_map.get("tracks")
    sections = analysis_map.get("songs")
    if not isinstance(tracks, list) or not isinstance(sections, list):
        raise ValueError("not an analysis map")
    spans: dict[int, tuple[float, float]] = {}
    for section in sections:  # pyright: ignore[reportUnknownVariableType]
        if not isinstance(section, Mapping):
            raise ValueError("not an analysis map")
        number = int(section["track"])  # pyright: ignore[reportUnknownArgumentType]
        start, end = float(section["start"]), float(section["end"])  # pyright: ignore[reportUnknownArgumentType]
        moved = track_shift_sec(analysis_map, number)
        start, end = start + moved, end + moved
        low, high = spans.get(number, (start, end))
        spans[number] = (min(low, start), max(high, end))
    out: list[PreviewSong] = []
    for track in tracks:  # pyright: ignore[reportUnknownVariableType]
        number = int(track["track"])  # pyright: ignore[reportUnknownArgumentType]
        if number not in spans:
            raise ValueError("a track without a section")
        start, end = spans[number]
        out.append(PreviewSong(start=round(start, 3), end=round(end, 3)))
    return out
