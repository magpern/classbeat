"""The revision of a ``refit_draft`` job (ADR 0007 amendment "beat-one corrections", item 6): the
base's content fitted again to the corrected music, with a provenance that names no model. Moved
out of ``classbeat.design.revision`` unchanged (SR C5, the repository's split-on-touch rule).
"""

from typing import Any, Literal
from uuid import UUID

from mowl import music as music_lib
from pydantic import BaseModel, ConfigDict, Field

from classbeat import __version__ as classbeat_version
from classbeat.agent import DesignResult
from classbeat.agent.records import mowl_version
from classbeat.design.music import MusicStep
from classbeat.design.revision import DraftRevision, content_and_music


class RefitProvenance(BaseModel):
    """``class_revisions.provenance`` of a ``refit_draft`` revision (ADR 0007 amendment
    "beat-one corrections", item 6): no model ran, so no model field, usage or cost."""

    model_config = ConfigDict(extra="forbid", strict=True)

    origin: Literal["beat_one"]
    base_revision_id: UUID
    classbeat_version: str = Field(min_length=1, max_length=64)
    mowl_version: str = Field(min_length=1, max_length=64)
    map_version: int = Field(ge=1)
    crossfade_sec: float = Field(ge=0, le=12)
    sport: Literal["running"] | None = None


def build_refit(
    result: DesignResult,
    music_map: dict[str, Any],
    step: MusicStep,
    *,
    base_revision_id: UUID,
    song_list_id: UUID | None = None,
    spotdown_list_id: UUID | None = None,
) -> DraftRevision:
    """The base's content fitted again to the corrected music. Stored even when the content is
    the base's again: its ``music.beat_one`` records the corrections now applied."""
    content, content_hash, music, running = content_and_music(
        result, music_map, step, song_list_id, spotdown_list_id
    )
    made = RefitProvenance(
        origin="beat_one",
        base_revision_id=base_revision_id,
        classbeat_version=classbeat_version,
        mowl_version=mowl_version(),
        map_version=int(music_map.get("version", music_lib.MAP_VERSION)),
        crossfade_sec=float(music_map.get("crossfade", 0.0)),
        sport="running" if running else None,
    )
    return DraftRevision(
        content=content,
        content_hash=content_hash,
        music=music.model_dump(mode="json", exclude_none=True),
        provenance=made.model_dump(mode="json", exclude_none=True),
        parent_id=base_revision_id,
    )
