"""A listening session of the SwedeBeat audition (ADR 0042 section 6.3): created by the user's Play
gesture, it authorizes clip requests for one class for 30 minutes.

``create`` runs in one user transaction: the studio on and the preview volume configured
(``feature_off``), the user's class (``not_found``: another user's or an unknown class) with a
current studio draft (``no_studio_draft``), then, under the owner's advisory lock (the jobs API's,
ADR 0005 section 2, so two presses count each other), at most ``SESSIONS_PER_HOUR`` sessions an
hour (``rate_limited``), then the row with ``expires_at = now() + LIFETIME`` (the database's
clock, as every check of it).

``locked`` reads a session for a clip request, ``FOR UPDATE`` (a clip's delivery count is
read-modify-write): only the user's own session **of that class**; anything else is ``None`` and
is answered like an unknown path.

Reasons are codes only; nothing of the rows is logged.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Final
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from classbeat.context import UserContext, require_context
from classbeat.db import user_session
from classbeat.jobs.user import lock_owner
from classbeat.preview import session_policy, storage
from classbeat.swedebeat_studio import current_draft
from classbeat.swedebeat_studio.admit import studio_on

_RECENT: Final = text(
    "SELECT count(*) FROM preview_sessions WHERE owner_id = :owner_id "
    "AND created_at > now() - interval '1 hour'"
)
_NEW: Final = text(
    "INSERT INTO preview_sessions (owner_id, class_id, expires_at) "
    "VALUES (:owner_id, :class_id, now() + make_interval(mins => :minutes)) "
    "RETURNING id, expires_at"
)
_LOCKED: Final = text(
    "SELECT id, expires_at, delivered_ms, delivered_keys, now() AS now FROM preview_sessions "
    "WHERE owner_id = :owner_id AND id = :id AND class_id = :class_id FOR UPDATE"
)
_COUNT: Final = text(
    "UPDATE preview_sessions SET delivered_ms = delivered_ms + :ms, "
    "delivered_keys = array_append(delivered_keys, :key) WHERE owner_id = :owner_id AND id = :id"
)


@dataclass(frozen=True, slots=True)
class Session:
    id: UUID
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class Locked:
    id: UUID
    expires_at: datetime
    delivered_ms: int
    delivered_keys: tuple[str, ...]
    now: datetime  # the database's clock, in the same statement


async def available(db: AsyncSession) -> bool:
    """The audition runs here: the studio's song stage and a preview volume."""
    return storage.current() is not None and await studio_on(db)


async def create(
    factory: async_sessionmaker[AsyncSession], ctx: UserContext, class_id: UUID
) -> Session | str:
    require_context(ctx)
    async with user_session(factory, ctx) as db:
        if not await available(db):
            return "feature_off"
        found = await current_draft.load(db, ctx.user_id, class_id)
        if isinstance(found, str):
            return "not_found" if found == "not_found" else "no_studio_draft"
        await lock_owner(db, ctx)
        recent = (await db.execute(_RECENT, {"owner_id": ctx.user_id})).scalar_one()
        if int(recent) >= session_policy.SESSIONS_PER_HOUR:
            return "rate_limited"
        minutes = int(session_policy.LIFETIME.total_seconds() // 60)
        params = {"owner_id": ctx.user_id, "class_id": class_id, "minutes": minutes}
        row = (await db.execute(_NEW, params)).one()
    return Session(row.id, row.expires_at)


async def locked(
    db: AsyncSession, owner_id: UUID, class_id: UUID, session_id: UUID
) -> Locked | None:
    params = {"owner_id": owner_id, "class_id": class_id, "id": session_id}
    row = (await db.execute(_LOCKED, params)).first()
    if row is None:
        return None
    keys = tuple(str(k) for k in row.delivered_keys)
    return Locked(row.id, row.expires_at, int(row.delivered_ms), keys, row.now)


async def count(db: AsyncSession, owner_id: UUID, session: Locked, key: str, ms: int) -> None:
    """Add a clip new to the session to its delivery count."""
    await db.execute(_COUNT, {"owner_id": owner_id, "id": session.id, "key": key, "ms": ms})
