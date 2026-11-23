"""``class_revisions.music`` of a SwedeBeat studio draft (ADR 0036 section 1; ADR 0040 section 6,
#1302). Pure: no I/O.

- ``StudioRevisionMusic``: the approved studio ``song_list`` the draft was designed on, the mix
  spec (``studio.mix_spec_model``, made by ``mix_plan_policy``), the composed map's version and,
  from #1302, ``analyses``: per track, **the analysis it was designed on** (``TrackRecord``).
  No link, fingerprint or MOWL object; its ``content_hash`` is ``revision.studio_hash``'s (the
  content and the mix spec), so a cut, the order or a crossfade is under it.
- ``TrackRecord``: the track's reference, the hex SHA-256 of the analysed bytes, the hex SHA-256
  of the map itself (``map_hash``: a re-analysis of the same bytes, say after a policy change, is
  another map) and spotdown's ``provenance_model`` record (the analysis id, versions, the
  audio identity, the timing class and its evidence; at most ``MAX_TRACKS`` records, each
  provenance read from a cache row, which the table caps at 64 KiB): the provenance kept per track
  in a class (plan section 4a), under the class's retention (ADR 0007 section 11), not only the
  30-day cache row's. A record stored before #1299 also names its analyser (always ``spotdown``,
  the only one since ADR 0040's amendment, #1356): that key is read and dropped (``_legacy``).
- ``wanted``: what a later step (revise, outputs, the timeline) must read for each reference:
  exactly the recorded bytes and map. A draft with no ``analyses`` (stored before #1302,
  when every track was analysed locally) wants nothing, so its tracks read as gone: the music
  changed (#1356, the local analysis and its rows are gone).
"""

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal, cast
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from classbeat.design.track_map_policy import MAX_TRACKS
from classbeat.studio.mix_spec_model import MixSpec


def drop_legacy_analyser(data: object) -> object:
    """A record stored before #1299 without its ``analyser: "spotdown"`` key (the only analyser
    since #1356); any other value stays, so ``extra="forbid"`` refuses it."""
    if isinstance(data, dict) and data.get("analyser") == "spotdown":  # pyright: ignore[reportUnknownMemberType]
        return {k: v for k, v in cast(dict[str, Any], data).items() if k != "analyser"}
    return data


class TrackRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    reference: str = Field(pattern=r"^[A-Za-z0-9_-]{1,64}$")
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    map_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    provenance: dict[str, Any] | None = None

    _legacy = model_validator(mode="before")(drop_legacy_analyser)


class StudioRevisionMusic(BaseModel):
    """See the module docstring."""

    model_config = ConfigDict(extra="forbid", strict=True)

    source: Literal["swedebeat_studio"]
    song_list_id: UUID
    mix_spec: MixSpec
    map_version: int = Field(ge=1)
    analyses: list[TrackRecord] | None = Field(default=None, max_length=MAX_TRACKS)


@dataclass(frozen=True, slots=True)
class Wanted:
    sha256: str
    map_hash: str


def map_hash(music_map: Mapping[str, Any]) -> str:
    """The hex SHA-256 of a track map's canonical JSON (sorted keys, compact)."""
    text = json.dumps(music_map, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(text.encode()).hexdigest()


def wanted(music: StudioRevisionMusic, references: list[str]) -> Mapping[str, Wanted]:
    """The row each of ``references`` must be read from; a reference without a record of a
    draft that has records is wanted from nothing (so it reads as gone: ``music_changed``)."""
    if music.analyses is None:
        return {}
    by_reference = {a.reference: a for a in music.analyses}
    return {
        r: Wanted(by_reference[r].sha256, by_reference[r].map_hash)
        for r in references
        if r in by_reference
    }
