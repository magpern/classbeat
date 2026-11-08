"""Passkeys in account recovery (ADR 0019 section 9, #775): the reset page's list and the deletes
of the reset and of ``admin mfa-reset``.

Both run **whatever ``CLASSBEAT_PASSKEYS`` says**: a passkey someone else planted must not survive
a reset because the feature was switched off later. The deletes run with the user row lock held
(``userlock.lock_user``, section 6), so a register, sign-in or re-auth finish either committed
before and is undone here, or waits and then finds its session or passkey gone. They return counts
only: the events record how many went, never an id, label or credential.
"""

from collections.abc import Collection
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from classbeat.auth.tables import webauthn_credentials


@dataclass(frozen=True, slots=True)
class ResetPasskey:
    """What the reset page shows of a passkey: no credential id, key or counter."""

    id: UUID
    label: str
    created_at: datetime
    last_used_at: datetime | None


async def passkeys_for_reset(db: AsyncSession, user_id: UUID) -> list[ResetPasskey]:
    """The user's passkeys, oldest first, for ``POST /password/reset/info``."""
    rows = await db.execute(
        select(
            webauthn_credentials.c.id,
            webauthn_credentials.c.label,
            webauthn_credentials.c.created_at,
            webauthn_credentials.c.last_used_at,
        )
        .where(webauthn_credentials.c.user_id == user_id)
        .order_by(webauthn_credentials.c.created_at, webauthn_credentials.c.id)
    )
    return [ResetPasskey(r.id, r.label, r.created_at, r.last_used_at) for r in rows.all()]


async def remove_passkeys_except(db: AsyncSession, user_id: UUID, keep: Collection[UUID]) -> int:
    """Delete every passkey of ``user_id`` whose id is not in ``keep``; return how many went.

    A keep list, not a remove list (section 9): a passkey registered after the page's ``info``
    was never shown, cannot be listed, and goes. An id in ``keep`` that is not the user's
    (unknown, foreign or removed meanwhile) matches nothing here, so it is ignored and touches
    no other user's row. An empty ``keep`` removes them all (``admin mfa-reset``). The caller
    holds the user row lock."""
    removed = await db.execute(
        delete(webauthn_credentials)
        .where(
            webauthn_credentials.c.user_id == user_id,
            webauthn_credentials.c.id.not_in(list(keep)),
        )
        .returning(webauthn_credentials.c.id)
    )
    return len(removed.all())
