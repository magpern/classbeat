"""The class's current studio draft, read back and re-checked (ADR 0036 sections 1, 5, 6; SR C6a).

``load`` reads the user's class (``owner_id`` and ``class_id`` together, under row security too)
with its ``current_revision_id`` and that revision, and returns a ``CurrentDraft`` only when all
of this holds, otherwise a reason:

- ``not_found``: no such class of the user (another user's class answers the same);
- ``no_studio_draft``: the class is not a studio class (``music_source``), has no current revision,
  or the current revision is no ``draft`` whose ``music`` parses as ``StudioRevisionMusic``;
- ``internal``: the stored ``content_hash`` is no longer ``revision_hash`` of ``{content,
  mix_spec}`` and its ``edit_overlay`` (ADR 0042 section 3: the one hash; a revision is immutable,
  so this never happens without tampering), its overlay does not read, or its approved studio
  ``song_list`` no longer loads (``song_list.load``, its own hash re-checked).

With ``lock`` the class row is share-locked until the caller commits (``FOR SHARE``), so the
current revision cannot be picked away between the check and the job's insert (ADR 0007 section 2:
``POST /current`` takes the row lock).
"""

import json
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from pydantic import ValidationError
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from classbeat.design.edit_overlay_model import EditOverlay, parsed
from classbeat.design.revision import StudioRevisionMusic
from classbeat.design.revision_hash import revision_hash
from classbeat.swedebeat_studio import song_list
from classbeat.swedebeat_studio.song_list_model import SOURCE

_SELECT = (
    "SELECT c.music_source ->> 'source' AS source, c.current_revision_id AS current, "
    "r.kind, r.content, r.content_hash, r.music, r.edit_overlay FROM classes c "
    "LEFT JOIN class_revisions r ON r.owner_id = c.owner_id AND r.class_id = c.id "
    "AND r.id = c.current_revision_id WHERE c.owner_id = :owner_id AND c.id = :class_id"
)
_READ = text(_SELECT)
_LOCKED = text(_SELECT + " FOR SHARE OF c")


@dataclass(frozen=True)
class CurrentDraft:
    revision_id: UUID
    content_hash: bytes
    content: dict[str, Any]
    music: StudioRevisionMusic
    songs: song_list.StudioList
    overlay: EditOverlay  # the stored ``edit_overlay`` (no record: NULL)

    @property
    def spec(self) -> dict[str, Any]:
        return self.music.mix_spec.model_dump(mode="json")


async def load(
    db: AsyncSession, owner_id: UUID, class_id: UUID, *, lock: bool = False
) -> CurrentDraft | str:
    """The current studio draft, or a reason (see the module docstring)."""
    params = {"owner_id": owner_id, "class_id": class_id}
    row = (await db.execute(_LOCKED if lock else _READ, params)).first()
    if row is None:
        return "not_found"
    if row.source != SOURCE or row.current is None or row.kind != "draft":
        return "no_studio_draft"
    if not isinstance(row.content, dict):
        return "no_studio_draft"
    content: dict[str, Any] = row.content  # pyright: ignore[reportUnknownVariableType]
    try:
        music = StudioRevisionMusic.model_validate_json(json.dumps(row.music))
    except ValidationError:
        return "no_studio_draft"
    stored = bytes(row.content_hash)
    try:
        overlay = parsed(row.edit_overlay)
    except ValueError:
        return "internal"
    spec = music.mix_spec.model_dump(mode="json")
    if revision_hash(content, mix_spec=spec, overlay=row.edit_overlay) != stored:
        return "internal"
    songs = await song_list.load(db, owner_id, class_id, music.song_list_id)
    if songs is None:
        return "internal"
    return CurrentDraft(row.current, stored, content, music, songs, overlay)
