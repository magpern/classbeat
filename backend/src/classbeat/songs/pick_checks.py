"""``pool_busy`` and the pick checks of both song kinds (ADR 0008 section 2, ``admit`` steps 3 to
5), split out of ``classbeat.songs.admit`` (CLAUDE.md, split on touch) so the composer's admit
(``composer_admit``) shares them.

A job **writes the pool** while it is a pick that is queued or running and not yet approved: mode
``pick``, and the composer's ``fill``, ``swap`` and ``change`` (ADR 0038 sections 5 and 6, #1188),
which all write and analyse the user's one working copy. A job with ``source`` (the user's own
playlist) and every approved phase never touch it.
"""

from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from classbeat import connections
from classbeat.context import UserContext
from classbeat.jobs.kinds import POOL_KIND_NAMES, Refused

# Another pick of the user that may still write or read the pool: not yet approved, still queued
# or running. ``revise_songs`` (SJ3) stores ``mode`` the same way.
_PICK_OPEN = text(
    "SELECT 1 FROM jobs WHERE owner_id = :owner_id AND kind = ANY(:kinds) "
    "AND state IN ('queued', 'running') AND approved_at IS NULL "
    "AND coalesce(input ->> 'mode', 'pick') IN ('pick', 'fill', 'swap', 'change') "
    "AND jsonb_typeof(input -> 'source') IS DISTINCT FROM 'object' LIMIT 1"
)


async def pool_busy(db: AsyncSession, owner_id: UUID) -> bool:
    found = await db.execute(_PICK_OPEN, {"owner_id": owner_id, "kinds": list(POOL_KIND_NAMES)})
    return found.first() is not None


async def pick_checks(
    db: AsyncSession, ctx: UserContext, spotify_connection_id: UUID
) -> Refused | None:
    """Steps 3 to 5 (both kinds' picks): ``not_connected``, ``consent_missing``, ``pool_busy``."""
    account = await connections.spotify_account_in(db, ctx, spotify_connection_id)
    if account is None:
        return Refused("not_connected")
    if not await connections.pool_consent_in(db, ctx, account):
        return Refused("consent_missing")
    if await pool_busy(db, ctx.user_id):
        return Refused("pool_busy")
    return None
