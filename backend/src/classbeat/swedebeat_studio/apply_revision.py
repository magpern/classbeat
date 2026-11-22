"""The revision an ``apply_mix_choices`` job stores (ADR 0036 amendment B, B4 and B10, #1369): the
derived spec and the moved, fitted workout, with a provenance that names no model.

- ``content``: the fitted draft (``revision.fitted_draft``), at most ``MAX_CONTENT_BYTES``;
- ``music``: ``StudioRevisionMusic`` on the same ``song_list`` with the derived spec, the composed
  map's version and the proposal's analysis records (the rows the facts were read from);
- ``content_hash``: ``revision_hash``: ``studio_hash`` of ``{content, mix_spec}``, unchanged
  (§1), or, for a base with an edit overlay (``mix_apply.Derivation.carried``, ADR 0042 section
  8), ``edited_hash`` over the carried overlay, which ``edit_overlay`` stores;
- ``provenance``: ``{origin: mix_choices, proposal_revision_id, choices}`` (the canonical choices,
  read back by the timeline to pre-fill the screen and by B9's ``mix_not_applied``) plus the
  versions; parent the base, the class's current draft when the job was admitted.
"""

import json
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from classbeat import __version__ as classbeat_version
from classbeat.agent.records import mowl_version
from classbeat.design import revision
from classbeat.design.revision import DraftRevision, StudioRevisionMusic
from classbeat.design.revision_hash import revision_hash
from classbeat.jobs import JobFailure
from classbeat.studio.mix_choice_model import MixChoices, canonical
from classbeat.studio.mix_proposal_policy import APPLIED_ORIGIN
from classbeat.studio.mix_spec_model import MixSpec
from classbeat.swedebeat_studio.draft import StudioMusic
from classbeat.swedebeat_studio.mix_apply_calc import Fitted
from classbeat.swedebeat_studio.studio_edit_calc import EditResult


class MixChoicesProvenance(BaseModel):
    """``class_revisions.provenance`` of an applied draft: no model ran, so no model field."""

    model_config = ConfigDict(extra="forbid", strict=True)

    origin: Literal["mix_choices"]
    proposal_revision_id: UUID
    choices: dict[str, Any]
    classbeat_version: str = Field(min_length=1, max_length=64)
    mowl_version: str = Field(min_length=1, max_length=64)
    map_version: int = Field(ge=1)
    tss_approximate: bool


def build(
    made: Fitted | EditResult,
    music: StudioMusic,
    song_list_id: UUID,
    choices: MixChoices,
    *,
    proposal_revision_id: UUID,
    base_revision_id: UUID,
) -> DraftRevision:
    if isinstance(made, Fitted):
        content = revision.fitted_draft(made.result).model_dump(mode="json", exclude_none=True)
        map_version, spec, overlay = int(made.composed.map["version"]), made.spec, None
        tss_approximate = made.result.tss_approximate
    else:
        if made.result is None or made.map_version is None:
            raise JobFailure("internal")
        content, map_version, spec = made.content, made.map_version, made.spec
        overlay, tss_approximate = made.overlay.stored(), made.result.tss_approximate
    if len(revision.canonical(content)) > revision.MAX_CONTENT_BYTES:
        raise JobFailure("invalid_output")
    stored = StudioRevisionMusic(
        source="swedebeat_studio",
        song_list_id=song_list_id,
        mix_spec=MixSpec.model_validate(spec),
        map_version=map_version,
        analyses=list(music.records),
    ).model_dump(mode="json")
    provenance = MixChoicesProvenance(
        origin=APPLIED_ORIGIN,
        proposal_revision_id=proposal_revision_id,
        choices=json.loads(canonical(choices)),
        classbeat_version=classbeat_version,
        mowl_version=mowl_version(),
        map_version=map_version,
        tss_approximate=tss_approximate,
    )
    return DraftRevision(
        content=content,
        content_hash=revision_hash(content, mix_spec=stored["mix_spec"], overlay=overlay),
        music=stored,
        provenance=provenance.model_dump(mode="json"),
        parent_id=base_revision_id,
        edit_overlay=overlay,
    )
