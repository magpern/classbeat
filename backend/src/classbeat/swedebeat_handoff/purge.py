"""Delete hand-off rows the hourly purge is old enough to drop (ADR 0016 section 7).

Runs in the caller's session (the purge's per-user transaction). Opens none of its own.
"""

from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from classbeat.swedebeat_handoff.config import PURGE_AFTER_DAYS

_DELETE = text(
    "DELETE FROM swedebeat_handoffs WHERE owner_id = :owner_id "
    "AND expires_at <= now() - make_interval(days => :days)"
)


async def purge_owner(db: AsyncSession, owner_id: UUID) -> int:
    """Rows of this user that have been expired for ``PURGE_AFTER_DAYS``. A live row stays."""
    result = await db.execute(_DELETE, {"owner_id": owner_id, "days": PURGE_AFTER_DAYS})
    return int(result.rowcount or 0)  # pyright: ignore[reportAttributeAccessIssue]
