"""Whether a class is still its owner's, for the "class ready" mail (ME5, #656).

The web process takes a hand-off row (``classbeat.emails.handoff``) and checks, just before it
sends, that the class the row names still exists and belongs to the row's owner: in the owner's
own session (row security), filtered on ``owner_id`` too. A class deleted meanwhile sends nothing.
"""

from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from classbeat.context import UserContext
from classbeat.db import user_session

_OWNED = text("SELECT 1 FROM classes WHERE id = :class_id AND owner_id = :owner_id")


async def still_owned(
    factory: async_sessionmaker[AsyncSession], ctx: UserContext, class_id: UUID
) -> bool:
    async with user_session(factory, ctx) as db:
        params = {"class_id": class_id, "owner_id": ctx.user_id}
        return (await db.execute(_OWNED, params)).first() is not None
