"""The user's own preferences (issue #525, ADR 0015 amendment of 2026-11-03): ``user_preferences``.

One row per user at most (migration 0028); no row is every preference at its default. Today it
holds one: ``uses_swedebeat``, "I use Swedebeat", **default off**. Every read and write runs as the
user (``user_session``, owner row security) and also filters on ``owner_id``.

The preference narrows, never widens: ``swedebeat_on`` is the effective ``swedebeat_enabled`` flag
(the environment's kill switch, then the admin flag, ADR 0011 section 7) **and** the preference.
``swedebeat_on`` reads the flag first and skips the user's row when it is off; the admissions read
the flag first too. ``GET /api/connections`` reads the stored value whatever the flag says, so the
account page shows the switch as stored. Disconnect and the account's export and deletion never
consult it: a user can always remove what is stored about them.
"""

from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from classbeat.config import Settings
from classbeat.context import UserContext
from classbeat.db import system_session, user_session
from classbeat.flags import effective

_READ = text("SELECT uses_swedebeat FROM user_preferences WHERE owner_id = :owner_id")
_WRITE = text(
    "INSERT INTO user_preferences (owner_id, uses_swedebeat) VALUES (:owner_id, :value) "
    "ON CONFLICT (owner_id) DO UPDATE SET uses_swedebeat = EXCLUDED.uses_swedebeat, "
    "updated_at = now() RETURNING uses_swedebeat"
)


async def uses_swedebeat_in(db: AsyncSession, owner_id: UUID) -> bool:
    """The preference in an open session of the user (false without a row)."""
    value = (await db.execute(_READ, {"owner_id": owner_id})).scalar_one_or_none()
    return value is True


async def uses_swedebeat(factory: async_sessionmaker[AsyncSession], ctx: UserContext) -> bool:
    async with user_session(factory, ctx) as db:
        return await uses_swedebeat_in(db, ctx.user_id)


async def set_uses_swedebeat(
    factory: async_sessionmaker[AsyncSession], ctx: UserContext, value: bool
) -> bool:
    """Store the preference for ``ctx``'s user (insert or update); returns what is stored."""
    async with user_session(factory, ctx) as db:
        stored = (await db.execute(_WRITE, {"owner_id": ctx.user_id, "value": value})).scalar_one()
    return stored is True


async def swedebeat_on(
    settings: Settings, factory: async_sessionmaker[AsyncSession], ctx: UserContext
) -> bool:
    """Whether Swedebeat is on for this user: the effective flag, then their preference."""
    async with system_session(factory) as db:
        if not await effective.swedebeat_enabled(db, settings.swedebeat_enabled):
            return False
    return await uses_swedebeat(factory, ctx)
