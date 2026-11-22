"""``class_revisions.music.spotdown`` of a draft on a Spotify playlist (ADR 0040 section 6, its
SD-P6 stage E amendment and its amendment "spotdown is the only analysis path"; #1303, #1407).
Pure: no I/O.

A draft whose design map had tracks on spotdown's sections (stamped ``approximate`` by stage D,
``track_timing_calc.stamp``) records **which** tracks (0-based positions, sorted) and where the
stage-B record they came from lives, exactly one of:

- ``song_list_id``: the ``song_list`` revision whose provenance holds the record (a pick, the
  user's own playlist, the composer);
- ``record``: the record itself (``{spotdown_match, spotdown_analysis}``, as a list's provenance
  holds it), for a ``design_draft`` on the user's MOWL playlist, which has no song list
  (``songs.draft_spotdown``, #1407). Like a mix draft's ``music.provenance`` (#1304) it names
  analysis ids, versions and audio identities; never a title, a Spotify id, a source reference, a
  scope or a URL. At most ``MAX_RECORD_BYTES`` as canonical JSON.

A later ``revise_draft`` or ``refit_draft`` reads those tracks again through the same verified
path (``songs.spotdown_carry``), so the class keeps the timing it was designed on. Every Spotify
draft has it (spotdown is the only analysis path): a base without it is ``music_changed``
(``design.spotdown_base``). A mix and a soundless class have none.
"""

import json
from typing import Annotated, Any, Final, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from classbeat.agent import track_timing_calc

MAX_TRACKS: Final = 200  # a revision's ``music.songs`` bound; a picked list has at most 40
Position = Annotated[int, Field(ge=0, lt=MAX_TRACKS)]
MAX_RECORD_BYTES: Final = 262144  # a playlist draft's own record, as canonical JSON (#1407)


class SpotdownSections(BaseModel):
    """The tracks a draft had on spotdown's sections and the list, or the record, that gave them."""

    model_config = ConfigDict(extra="forbid", strict=True)

    song_list_id: UUID | None = None
    tracks: list[Position] = Field(min_length=1, max_length=MAX_TRACKS)
    record: dict[str, Any] | None = None

    @model_validator(mode="after")
    def _sorted_once(self) -> Self:
        if self.tracks != sorted(set(self.tracks)):
            raise ValueError("tracks are sorted and each is named once")
        return self

    @model_validator(mode="after")
    def _one_origin(self) -> Self:
        if (self.song_list_id is None) == (self.record is None):
            raise ValueError("exactly one of a song list and a record")
        if self.record is not None:
            size = len(json.dumps(self.record, sort_keys=True, separators=(",", ":")).encode())
            if size > MAX_RECORD_BYTES:
                raise ValueError("the record is too large")
        return self


def stamped(m: object) -> list[int]:
    """The 0-based positions of a Spotify map's tracks on spotdown's sections (``approximate``)."""
    if not isinstance(m, dict) or not isinstance(m.get("tracks"), list):
        return []
    found = track_timing_calc.classes(m)  # pyright: ignore[reportUnknownArgumentType]
    return sorted(i for i, cls in found.items() if cls == "approximate")


def sections_of(
    m: object, song_list_id: UUID | None, origin: SpotdownSections | None = None
) -> SpotdownSections | None:
    """The field for a draft fitted to ``m``: ``None`` when no track is stamped. ``origin`` (the
    music step's: a revise's carry, a playlist design's own record) names where the record lives,
    else ``song_list_id``. A stamped map with neither is impossible (only a record stamps a
    track): ``ValueError``."""
    tracks = stamped(m)
    if not tracks:
        return None
    if origin is not None:
        return SpotdownSections(
            song_list_id=origin.song_list_id, record=origin.record, tracks=tracks
        )
    if song_list_id is None:
        raise ValueError("a stamped track without its song list")
    return SpotdownSections(song_list_id=song_list_id, tracks=tracks)
