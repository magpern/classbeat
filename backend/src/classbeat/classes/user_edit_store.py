"""The revision a user edit stores, and the class pointer moved to it (ADR 0042 sections 3 and 6,
#1391). Called by ``classes.user_edit`` inside its one transaction, the class row locked.

- ``content``: the edited workout (``studio_edit_ops``), at most ``MAX_CONTENT_BYTES``;
- ``music``: the base's ``StudioRevisionMusic`` with the edited spec (a workout edit leaves it as
  it is; a pin edit stores the pinned spec), the same ``song_list``, map version and analyses;
- ``edit_overlay``: the overlay after the edit, NULL when it has no record;
- ``content_hash``: ``revision_hash`` of the three (the one hash, ``design.revision_hash``);
- ``provenance``: ``{origin: user_edit, base_revision_id, edit: <op>}`` plus the versions; no model
  ran, so no model field. Parent the base.

``store`` inserts the row (``kind = 'draft'``, no job: ``job_id`` NULL, the next ``number`` of the
class) and moves ``current_revision_id`` from the base to it only while the base is still the
current revision (compare and set; ``False``: nothing moved, the caller rolls back).
"""

import json
import uuid
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from classbeat import __version__ as classbeat_version
from classbeat.agent.records import mowl_version
from classbeat.design.revision import MAX_CONTENT_BYTES, StudioRevisionMusic, canonical
from classbeat.design.revision_hash import revision_hash
from classbeat.design.workout_edit_policy import EditRefused
from classbeat.studio.mix_proposal_policy import USER_EDIT_ORIGIN
from classbeat.studio.mix_spec_model import MixSpec
from classbeat.swedebeat_studio.studio_edit_calc import EditResult

_INSERT = text(
    "INSERT INTO class_revisions (id, owner_id, class_id, number, kind, content, content_hash, "
    "music, provenance, job_id, parent_id, edit_overlay) "
    "SELECT :id, :owner_id, :class_id, coalesce(max(r.number), 0) + 1, 'draft', "
    "CAST(:content AS jsonb), :content_hash, CAST(:music AS jsonb), CAST(:provenance AS jsonb), "
    "NULL, :parent_id, CAST(:edit_overlay AS jsonb) FROM class_revisions r "
    "WHERE r.owner_id = :owner_id AND r.class_id = :class_id RETURNING number"
)
_MOVE = text(
    "UPDATE classes SET current_revision_id = :id, updated_at = now() "
    "WHERE id = :class_id AND owner_id = :owner_id AND current_revision_id = :parent_id "
    "RETURNING id"
)
# What the cap counts (``REVISION_LIMIT``): the class's revisions a user edit stored.
COUNT_EDITS = text(
    "SELECT count(*) FROM class_revisions WHERE owner_id = :owner_id AND class_id = :class_id "
    "AND provenance ->> 'origin' = 'user_edit'"
)
REVISION_LIMIT = 200


class UserEditProvenance(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    origin: Literal["user_edit"]
    base_revision_id: UUID
    edit: str = Field(min_length=1, max_length=32)
    classbeat_version: str = Field(min_length=1, max_length=64)
    mowl_version: str = Field(min_length=1, max_length=64)
    map_version: int = Field(ge=1)
    tss_approximate: bool


class Stored(BaseModel):
    revision_id: UUID
    number: int
    content_hash: bytes


def _music(base: StudioRevisionMusic, spec: dict[str, Any]) -> dict[str, Any]:
    edited = base.model_copy(update={"mix_spec": MixSpec.model_validate(spec)})
    return StudioRevisionMusic.model_validate(edited.model_dump()).model_dump(mode="json")


async def store(
    db: AsyncSession,
    owner_id: UUID,
    class_id: UUID,
    base_id: UUID,
    base_music: StudioRevisionMusic,
    made: EditResult,
    op: str,
) -> Stored | None:
    """The edited revision stored and made current (module docstring); ``None`` when the base
    is no longer current (nothing moved)."""
    if len(canonical(made.content)) > MAX_CONTENT_BYTES:
        raise EditRefused("invalid_edit")
    music = _music(base_music, made.spec)
    overlay = made.overlay.stored()
    content_hash = revision_hash(made.content, mix_spec=music["mix_spec"], overlay=overlay)
    provenance = UserEditProvenance(
        origin=USER_EDIT_ORIGIN,
        base_revision_id=base_id,
        edit=op,
        classbeat_version=classbeat_version,
        mowl_version=mowl_version(),
        map_version=base_music.map_version,
        tss_approximate=made.result.tss_approximate if made.result else False,
    )
    revision_id = uuid.uuid4()
    params = {"id": revision_id, "owner_id": owner_id, "class_id": class_id}
    found = (
        await db.execute(
            _INSERT,
            {
                **params,
                "content": json.dumps(made.content, sort_keys=True, ensure_ascii=False),
                "content_hash": content_hash,
                "music": json.dumps(music, sort_keys=True, ensure_ascii=False),
                "provenance": json.dumps(provenance.model_dump(mode="json"), sort_keys=True),
                "parent_id": base_id,
                "edit_overlay": None if overlay is None else json.dumps(overlay, sort_keys=True),
            },
        )
    ).first()
    if found is None:  # the class has its base revision, so a row always comes back
        raise RuntimeError("the edited revision was not stored")
    moved = (await db.execute(_MOVE, {**params, "parent_id": base_id})).first()
    if moved is None:
        return None
    return Stored(revision_id=revision_id, number=found.number, content_hash=content_hash)
