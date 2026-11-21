"""``class_revisions.music.spotdown`` of a draft on a Spotify playlist (ADR 0040 section 6 and its
SD-P6 stage E amendment; #1303). Pure: no I/O.

A draft whose design map had at least one track on spotdown's sections (``songs.phase2_spotdown``:
the track stamped ``approximate`` by ``track_timing_calc.stamp``) records **which** tracks (0-based
positions, sorted) and the ``song_list`` revision whose ``spotdown_analysis`` record they came
from. A later ``revise_draft`` or ``refit_draft`` of that draft reads those tracks again through
the same verified path (``songs.spotdown_carry``), so the class keeps the timing it was designed
on; a track the draft had on MOWL's sections stays on them.

Absent on every other draft, which therefore stores exactly as before: no track on spotdown's
sections, spotdown off, a mix, a soundless class. Never a title, an analysis id, a source or a URL.
"""

from typing import Annotated, Final, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from classbeat.agent import track_timing_calc

MAX_TRACKS: Final = 200  # a revision's ``music.songs`` bound; a picked list has at most 40
Position = Annotated[int, Field(ge=0, lt=MAX_TRACKS)]


class SpotdownSections(BaseModel):
    """The tracks a draft had on spotdown's sections and the list whose record gave them."""

    model_config = ConfigDict(extra="forbid", strict=True)

    song_list_id: UUID
    tracks: list[Position] = Field(min_length=1, max_length=MAX_TRACKS)

    @model_validator(mode="after")
    def _sorted_once(self) -> Self:
        if self.tracks != sorted(set(self.tracks)):
            raise ValueError("tracks are sorted and each is named once")
        return self


def stamped(m: object) -> list[int]:
    """The 0-based positions of a Spotify map's tracks on spotdown's sections (``approximate``)."""
    if not isinstance(m, dict) or not isinstance(m.get("tracks"), list):
        return []
    found = track_timing_calc.classes(m)  # pyright: ignore[reportUnknownArgumentType]
    return sorted(i for i, cls in found.items() if cls == "approximate")


def sections_of(m: object, song_list_id: UUID | None) -> SpotdownSections | None:
    """The record for a draft fitted to ``m``: ``None`` when no track is stamped. A stamped map
    without a list is impossible (only a list's record stamps a track): ``ValueError``."""
    tracks = stamped(m)
    if not tracks:
        return None
    if song_list_id is None:
        raise ValueError("a stamped track without its song list")
    return SpotdownSections(song_list_id=song_list_id, tracks=tracks)
