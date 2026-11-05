"""The user row lock that orders sign-in, re-authentication, linking and reset (#639).

ADR 0019 section 6: every transaction that creates or rotates a session, links an identity or
revokes a user's sessions on the reset path takes ``SELECT ... FROM users WHERE id = ... FOR
UPDATE`` as its first lock (a reset consumes its token first, which locks only the token row).

Why ``FOR UPDATE`` and not the locks the statements take anyway: inserting a session or an
identity takes only ``FOR KEY SHARE`` on ``users`` and the reset's ``UPDATE users`` only ``FOR NO
KEY UPDATE``, which do not conflict. A reset could then commit while a sign-in that checked the old
password is still inserting its session, and its ``DELETE FROM sessions`` would not see that row.
With both sides holding ``FOR UPDATE`` one waits for the other: a finish that commits first is
undone by the reset, and a finish that waits re-checks what it proved (the password hash, the
session, the identity) under the lock and stops if the reset changed it. Password and signature
checks never run while the lock is held.
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
    """Lock the user's row (``FOR UPDATE``) and read it as committed now; None if it is gone."""
    row = (
        await db.execute(
            select(users.c.password_hash, users.c.disabled_at)
            .where(users.c.id == user_id)
            .with_for_update()
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
