"""Who may buy (ADR 0031 section 6), checked at every request and never cached.

With billing off (all of M7a), a caller whose stored role is `admin` or who holds an active
`billing_test` entitlement. The same function runs twice per action: first on the app role's
session (no maint connection is opened for anyone else), then again on the maint session inside
the write transaction, so a grant revoked in between still refuses (a time-of-check gap).
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from classbeat import auth

_TESTER = text(
    "SELECT 1 FROM entitlements e WHERE e.owner_id = :o AND 'billing_test' = ANY (e.features) "
    "AND e.revoked_at IS NULL AND e.starts_at <= now() "
    "AND (e.ends_at IS NULL OR e.ends_at > now()) LIMIT 1"
)


async def may_buy(db: AsyncSession, user_id: UUID, *, billing_enabled: bool = False) -> bool:
    """`True` for everyone when billing is on (never in M7a), else for a stored admin or an
    active tester. `db` is the app role's user session or the maint session; both name
    `owner_id` explicitly."""
    if billing_enabled:
        return True
    if await auth.stored_admin(db, user_id):
        return True
    return (await db.execute(_TESTER, {"o": user_id})).first() is not None
