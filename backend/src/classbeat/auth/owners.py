"""Account checks for the job issuer (ADR 0005 section 3) and the "class ready" mail (ME5):
``classbeat.jobs`` and ``classbeat.emails.handoff`` ask here, because only ``classbeat.auth``
touches the account tables (guard 8)."""

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
