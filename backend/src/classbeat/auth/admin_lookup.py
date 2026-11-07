"""Account emails for the admin area's Usage, Jobs and Abuse views (ADR 0011 section 6, AD3 #221;
``account_states`` for the Abuse view, #632).

Guard 8 keeps every statement on the account tables in this package, so ``classbeat.admin.usage``
and ``classbeat.admin.jobs`` call ``account_emails`` with the session they hold: the audited
``admin_session`` on ``classbeat_maint``, whose column grants allow reading ``users.id`` and
``users.email``. Nothing here opens a session or reads anything else of an account.
"""

from collections.abc import Collection
from datetime import datetime
from typing import NamedTuple
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from classbeat.auth.tables import users

MAX_IDS = 1000


async def account_emails(db: AsyncSession, ids: Collection[UUID]) -> dict[UUID, str]:
    """``{user id: email}`` for those of ``ids`` that are accounts (at most ``MAX_IDS``)."""
    wanted = sorted(set(ids))
    if not wanted:
        return {}
    if len(wanted) > MAX_IDS:
        raise ValueError(f"at most {MAX_IDS} accounts per lookup")
    rows = await db.execute(select(users.c.id, users.c.email).where(users.c.id.in_(wanted)))
    return {row.id: row.email for row in rows.all()}


class AccountState(NamedTuple):
    """What the Abuse view needs to offer a shortcut: the email, the role and whether the account
    is disabled (all three columns maint already reads)."""

    email: str
    role: str
    disabled_at: datetime | None


async def account_states(db: AsyncSession, ids: Collection[UUID]) -> dict[UUID, AccountState]:
    """``{user id: AccountState}`` for those of ``ids`` that are accounts (at most ``MAX_IDS``)."""
    wanted = sorted(set(ids))
    if not wanted:
        return {}
    if len(wanted) > MAX_IDS:
        raise ValueError(f"at most {MAX_IDS} accounts per lookup")
    rows = await db.execute(
        select(users.c.id, users.c.email, users.c.role, users.c.disabled_at).where(
            users.c.id.in_(wanted)
        )
    )
    return {r.id: AccountState(r.email, r.role, r.disabled_at) for r in rows.all()}
