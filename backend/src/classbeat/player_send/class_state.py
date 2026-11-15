"""What the player holds of one class (ADR 0034 section 6), read for ``GET /api/classes/{id}``
and for the class delete, in the caller's transaction under the owner's row security.

Reads only: no ``FOR UPDATE`` and no ``FOR SHARE``, so a page load never waits on a send, a
removal or a connection change.

- ``sent_state``: the revision the player holds, or ``None`` when the class was never sent, was
  removed, or the player is not connected.
- ``has_live_publication``: a publication row that a delete must act on (a number was taken and
  the row is not removed); ``False`` for a class that was never sent, so the delete takes no
  lock on the connection row for it.
"""

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

_SENT = text(
    "SELECT r.id AS revision_id, r.number AS revision_number FROM player_publications p "
    "JOIN class_revisions r ON r.id = p.sent_revision_id AND r.owner_id = p.owner_id "
    "WHERE p.owner_id = :owner_id AND p.source_class_id = :class_id AND p.removed_at IS NULL "
    "AND EXISTS (SELECT 1 FROM connections c WHERE c.owner_id = p.owner_id "
    "AND c.provider = 'player' AND c.status = 'connected')"
)
_LIVE = text(
    "SELECT 1 FROM player_publications WHERE owner_id = :owner_id AND source_class_id = :class_id "
    "AND removed_at IS NULL AND last_number > 0"
)


@dataclass(frozen=True, slots=True)
class SentState:
    """The revision the player holds (ADR 0034 section 6, #1068): ``GET /api/classes/{id}``'s
    ``player``."""

    sent_revision_id: UUID
    sent_revision_number: int


async def sent_state(db: AsyncSession, owner_id: UUID, class_id: UUID) -> SentState | None:
    row = (await db.execute(_SENT, {"owner_id": owner_id, "class_id": class_id})).first()
    return (
        None
        if row is None
        else SentState(
            sent_revision_id=row.revision_id, sent_revision_number=int(row.revision_number)
        )
    )


async def has_live_publication(db: AsyncSession, owner_id: UUID, class_id: UUID) -> bool:
    params = {"owner_id": owner_id, "class_id": class_id}
    return (await db.execute(_LIVE, params)).first() is not None
