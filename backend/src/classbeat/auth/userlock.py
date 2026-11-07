"""The user row lock that orders sign-in, re-authentication, linking, reset and revokes (#639).

ADR 0019 section 6: every transaction that creates or rotates a session, links an identity or
revokes a user's sessions on the reset, sign-out (``POST /api/auth/logout``, #646), sign out one
session (``DELETE /api/auth/sessions/{id}``, #678), sign-out-everywhere, ``admin mfa-reset``,
``keys wipe`` TOTP-reset (#643) or TOTP-removal (#675) path takes ``SELECT ... FROM users WHERE id
= ... FOR NO KEY UPDATE`` as its first lock (a reset consumes its token first, which locks only
the token row).
The admin disable and every account delete take the same mode through ``admin_accounts``
(``get_account``, ``delete_account``; #675), which cannot read ``password_hash``.

Why an explicit lock, not the locks the statements take anyway: inserting a session or an
identity takes only ``FOR KEY SHARE`` on ``users``, which does not conflict with the reset's
``UPDATE users``. A reset could then commit while a sign-in that checked the old password is
still inserting its session, and its ``DELETE FROM sessions`` would not see that row. With both
sides taking this same explicit lock one waits for the other: a finish that commits first is
undone by the reset, and a finish that waits re-checks what it proved (the password hash, the
session, the identity) under the lock and stops if the reset changed it. Password and signature
checks never run while the lock is held.

Why ``NO KEY UPDATE`` and not ``FOR UPDATE`` (#639 review): two ``NO KEY UPDATE`` locks conflict,
which is all the ordering above needs, but neither blocks the ``FOR KEY SHARE`` that the foreign
key check of any insert referencing ``users`` takes. ``FOR UPDATE`` would: the session middleware
locks a session row and then inserts an ``auth_events`` row for its user (a new network or
browser, ``sessions._compare_mark``), while a reset or rotation holding the user lock waits for
that session row, which is a deadlock.
"""

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from classbeat.auth.tables import sessions, users


@dataclass(frozen=True, slots=True)
class LockedUser:
    password_hash: str | None
    disabled: bool


async def lock_user(db: AsyncSession, user_id: UUID) -> LockedUser | None:
    """Lock the user's row (``FOR NO KEY UPDATE``) and read it as committed now; None if gone."""
    row = (
        await db.execute(
            select(users.c.password_hash, users.c.disabled_at)
            .where(users.c.id == user_id)
            .with_for_update(key_share=True)  # FOR NO KEY UPDATE: see the module docstring
        )
    ).first()
    return None if row is None else LockedUser(row.password_hash, row.disabled_at is not None)


async def session_alive(db: AsyncSession, user_id: UUID, session_id: UUID) -> bool:
    """Whether the session still exists for the user and is not past its absolute expiry. Called
    under ``lock_user``: a reset that committed meanwhile deleted it."""
    found = await db.execute(
        select(sessions.c.id).where(
            sessions.c.id == session_id,
            sessions.c.user_id == user_id,
            sessions.c.expires_at > func.now(),
        )
    )
    return found.first() is not None
