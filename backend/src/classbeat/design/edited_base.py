"""The refusal of an AI change to an edited Spotify draft (``design.edited_base_calc``).

One read of the base revision under the owner's row-level security, before anything is written
(no message, no job, no credit). Used by ``revise_draft``, ``revise_songs`` and ``refit_draft``.
"""

from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from classbeat.design.edited_base_calc import REASON, is_edited_spotify
from classbeat.jobs.kinds import Refused

_BASE = text(
    "SELECT music ->> 'source' AS source, provenance ->> 'origin' AS origin, edit_overlay "
    "FROM class_revisions WHERE owner_id = :owner_id AND class_id = :class_id AND id = :id"
)


async def refuse_edited(
    db: AsyncSession, owner_id: UUID, class_id: UUID, base: UUID
) -> Refused | None:
    """``has_user_edits`` when ``base`` is an edited Spotify draft, else ``None``."""
    row = (
        await db.execute(_BASE, {"owner_id": owner_id, "class_id": class_id, "id": base})
    ).first()
    if row is None or not is_edited_spotify(row.source, row.origin, row.edit_overlay):
        return None
    return Refused(REASON)
