"""``class_revisions.music`` of a SwedeBeat studio draft (ADR 0036 section 1; ADR 0040 section 6,
#1302). Pure: no I/O.

- ``StudioRevisionMusic``: the approved studio ``song_list`` the draft was designed on, the mix
  spec (``studio.mix_spec_model``, made by ``mix_plan_policy``), the composed map's version and,
  from #1302, ``analyses``: per track, **the analysis it was designed on** (``TrackRecord``).
  No link, fingerprint or MOWL object; its ``content_hash`` is ``revision.studio_hash``'s (the
  content and the mix spec), so a cut, the order or a crossfade is under it.
- ``TrackRecord``: the track's reference, its analyser (``local`` or ``spotdown``), the hex
  SHA-256 of the analysed bytes, the hex SHA-256 of the map itself (``map_hash``: a re-analysis
  of the same bytes by the same analyser, say after a policy change, is another map) and, for
  spotdown, ``provenance_model``'s record (the analysis id, versions, the audio identity, the
  timing class and its evidence): the provenance kept per track in a class (plan section 4a),
  under the class's retention (ADR 0007 section 11), not only the 30-day cache row's.
- ``wanted``: what a later step (revise, outputs, the timeline) must read for each reference:
  exactly the recorded analyser, bytes and map. A draft stored before #1302 has no ``analyses``; its
  tracks were all analysed locally, so it wants the ``local`` row, of any bytes, as before.
"""

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from classbeat.studio.mix_spec_model import MixSpec

Analyser = Literal["local", "spotdown"]


class TrackRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    reference: str = Field(pattern=r"^[A-Za-z0-9_-]{1,64}$")
    analyser: Analyser
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    map_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    provenance: dict[str, Any] | None = None


class StudioRevisionMusic(BaseModel):
    """See the module docstring."""

    model_config = ConfigDict(extra="forbid", strict=True)

    source: Literal["swedebeat_studio"]
    song_list_id: UUID
    mix_spec: MixSpec
    map_version: int = Field(ge=1)
    analyses: list[TrackRecord] | None = None


@dataclass(frozen=True, slots=True)
class Wanted:
    analyser: Analyser
    sha256: str | None  # None: any bytes and map (a draft stored before #1302)
    map_hash: str | None = None


def map_hash(music_map: Mapping[str, Any]) -> str:
    """The hex SHA-256 of a track map's canonical JSON (sorted keys, compact)."""
    text = json.dumps(music_map, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(text.encode()).hexdigest()


def wanted(music: StudioRevisionMusic, references: list[str]) -> Mapping[str, Wanted]:
    """The row each of ``references`` must be read from; a reference without a record of a
    draft that has records is wanted from nothing (so it reads as gone: ``music_changed``)."""
    if music.analyses is None:
        return {r: Wanted("local", None) for r in references}
    by_reference = {a.reference: a for a in music.analyses}
    return {
        r: Wanted(by_reference[r].analyser, by_reference[r].sha256, by_reference[r].map_hash)
        for r in references
        if r in by_reference
    }
