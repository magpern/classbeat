"""Account emails for the admin area's Usage, Jobs and Abuse views (ADR 0011 section 6, AD3 #221;
``account_states`` for the Abuse view, #632).

Guard 8 keeps every statement on the account tables in this package, so ``classbeat.admin.usage``
and ``classbeat.admin.jobs`` call ``account_emails`` with the session they hold: the audited
``admin_session`` on ``classbeat_maint``, whose column grants allow reading ``users.id`` and
``users.email``. Nothing here opens a session or reads anything else of an account.

``addresses_with_accounts`` and ``invite_states`` are the invite requests' (ADR 0033): which
requested addresses already have an account (``users.email_normalized``) and what became of the
invites issued for them (``invites.used_at`` and ``expires_at``, never the code's hash).
"""

from collections.abc import Collection
from datetime import datetime
from typing import NamedTuple
from uuid import UUID

from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from classbeat.auth.tables import invites, users

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


async def addresses_with_accounts(db: AsyncSession, normalized: Collection[str]) -> set[str]:
    """Those of the normalized addresses ``normalized`` that are accounts (at most ``MAX_IDS``)."""
    wanted = sorted(set(normalized))
    if not wanted:
        return set()
    if len(wanted) > MAX_IDS:
        raise ValueError(f"at most {MAX_IDS} addresses per lookup")
    rows = await db.execute(
        select(users.c.email_normalized).where(users.c.email_normalized.in_(wanted))
    )
    return set(rows.scalars().all())


async def invite_states(db: AsyncSession, ids: Collection[UUID]) -> dict[UUID, str]:
    """``{invite id: active, used or expired}`` for those of ``ids`` that are invites."""
    wanted = sorted(set(ids))
    if not wanted:
        return {}
    if len(wanted) > MAX_IDS:
        raise ValueError(f"at most {MAX_IDS} invites per lookup")
    state = case(
        (invites.c.used_at.is_not(None), "used"),
        (invites.c.expires_at <= func.now(), "expired"),
        else_="active",
    )
    rows = await db.execute(select(invites.c.id, state).where(invites.c.id.in_(wanted)))
    return {row[0]: row[1] for row in rows.all()}
