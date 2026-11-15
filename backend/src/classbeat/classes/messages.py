"""The class's design chat as the class API reads it: the user's own messages of one
class, in order, at most ``MESSAGE_LIMIT``. One ``user_session`` per call."""

from typing import Any, Final
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from classbeat.context import UserContext, require_context
from classbeat.db import user_session

Factory = async_sessionmaker[AsyncSession]

MESSAGE_LIMIT: Final = 500

_CLASS_EXISTS = text("SELECT 1 FROM classes WHERE id = :class_id AND owner_id = :owner_id")
_MESSAGES = text(
    "SELECT seq, role, body, job_id, base_revision_id, result_revision_id, change, created_at "
    "FROM class_messages WHERE owner_id = :owner_id AND class_id = :class_id "
    "ORDER BY seq LIMIT :limit"
)


async def list_messages(factory: Factory, ctx: UserContext, class_id: UUID) -> list[Any] | None:
    """The messages, or None when the user has no such class."""
    require_context(ctx)
    params = {"owner_id": ctx.user_id, "class_id": class_id}
    async with user_session(factory, ctx) as db:
        if (await db.execute(_CLASS_EXISTS, params)).first() is None:
            return None
        return list((await db.execute(_MESSAGES, {**params, "limit": MESSAGE_LIMIT})).all())
