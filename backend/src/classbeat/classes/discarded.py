"""The class page's answer for a class a failed first creation discarded (ADR 0038).

``GET /api/classes/{id}`` of a class that is not there asks here: when the user's own ended job
names it as ``discarded_class_id`` (``classbeat.jobs.discard``), the 404 says ``class_discarded``
and carries that job's id, so the page can tell why it stopped and offer the request again.
Another user's id, an unknown one and a class deleted by hand stay a plain ``not_found``: the
read runs as the user under RLS and filters on ``owner_id`` too.
"""

from uuid import UUID

from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from classbeat.context import UserContext, require_context
from classbeat.db import user_session

_ENDED = text(
    "SELECT id FROM jobs WHERE owner_id = :owner_id AND discarded_class_id = :class_id "
    "ORDER BY finished_at DESC, id LIMIT 1"
)


async def discarded_job(
    factory: async_sessionmaker[AsyncSession], ctx: UserContext, class_id: UUID
) -> UUID | None:
    """The user's ended job whose class ``class_id`` was discarded, if any."""
    require_context(ctx)
    async with user_session(factory, ctx) as db:
        params = {"owner_id": ctx.user_id, "class_id": class_id}
        return (await db.execute(_ENDED, params)).scalar_one_or_none()


async def not_found_answer(
    factory: async_sessionmaker[AsyncSession], ctx: UserContext, class_id: UUID
) -> JSONResponse:
    job_id = await discarded_job(factory, ctx, class_id)
    if job_id is None:
        return JSONResponse({"detail": {"reason": "not_found"}}, status_code=404)
    detail = {"reason": "class_discarded", "job_id": str(job_id)}
    return JSONResponse({"detail": detail}, status_code=404)
