"""Account checks for the job issuer (ADR 0005 section 3) and the "class ready" mail (ME5):
``classbeat.jobs`` and ``classbeat.emails.handoff`` ask here, because only ``classbeat.auth``
touches the account tables (guard 8)."""

from collections.abc import Collection
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from classbeat.auth.tables import users


async def owner_active(db: AsyncSession, user_id: UUID) -> bool:
    """Whether the user exists and is not disabled. ``db`` is a ``system_session``."""
    row = (
        await db.execute(
            select(users.c.id).where(users.c.id == user_id, users.c.disabled_at.is_(None))
        )
    ).first()
    return row is not None


async def active_email(db: AsyncSession, user_id: UUID) -> str | None:
    """The address of the user if the account exists and is not disabled, else ``None``. For the
    web process's "class ready" mail (ME5), which knows only the owner's id. ``db`` is a
    ``system_session``."""
    return (
        await db.execute(
            select(users.c.email).where(users.c.id == user_id, users.c.disabled_at.is_(None))
        )
    ).scalar_one_or_none()


async def stored_admin(db: AsyncSession, user_id: UUID) -> bool:
    """Whether the stored ``users.role`` of an existing, enabled user is ``admin``; ``False`` for
    no row (a disabled or missing user), never an error and never ``True`` then. For the plan
    resolver (ADR 0031 section 2): the plan sets usage limits and grants no authority, so the
    stored role is enough and no session or MFA state is needed (the worker has none). ``db`` is
    the caller's session, read in the caller's transaction, so a demotion applies on the next
    statement."""
    role = (
        await db.execute(
            select(users.c.role).where(users.c.id == user_id, users.c.disabled_at.is_(None))
        )
    ).scalar_one_or_none()
    return role == "admin"


async def stored_admins(db: AsyncSession, user_ids: Collection[UUID]) -> frozenset[UUID]:
    """The ids among ``user_ids`` for which ``stored_admin`` answers ``True`` (an existing,
    enabled user whose stored role is ``admin``), in one statement whatever their number. For the
    admin area's Users list, which shows each user's plan as the resolver does (ADR 0031
    section 2) without a lookup per row. ``db`` is the caller's session."""
    if not user_ids:
        return frozenset()
    rows = await db.execute(
        select(users.c.id).where(
            users.c.id.in_(list(user_ids)),
            users.c.role == "admin",
            users.c.disabled_at.is_(None),
        )
    )
    return frozenset(rows.scalars().all())


async def lock_owner_row(db: AsyncSession, user_id: UUID) -> None:
    """Lock the user's row for the rest of the transaction without changing it, so one owner's
    billing writes (ADR 0031 section 3) run one after the other. `FOR NO KEY UPDATE` does not
    block the foreign keys of the owner's other rows. ``db`` is the maint role's session."""
    await db.execute(
        select(users.c.id).where(users.c.id == user_id).with_for_update(key_share=True)
    )
