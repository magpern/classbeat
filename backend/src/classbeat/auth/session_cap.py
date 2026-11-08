"""Shorten a just-issued session's absolute limit (ADR 0019, owner question 4, W9, #776).

``cap_session`` runs in the sign-in's own transaction, right after ``start_session``. It only
ever lowers ``expires_at`` (``LEAST`` with the stored value, so a session that inherited an
earlier limit keeps it), measured from the database's ``now()`` like every other session time.
Rotation (``rotate_session``) and a later sign-in replacing this session (``start_session``)
copy ``expires_at``, so the shorter limit survives both.
"""

from datetime import timedelta

from sqlalchemy import func, update
from sqlalchemy.ext.asyncio import AsyncSession

from classbeat.auth.sessions import IssuedSession
from classbeat.auth.tables import sessions


async def cap_session(db: AsyncSession, issued: IssuedSession, limit: timedelta) -> IssuedSession:
    """``issued`` with ``expires_at`` at most ``limit`` from now; the token is unchanged."""
    row = (
        await db.execute(
            update(sessions)
            .where(sessions.c.id == issued.id)
            .values(expires_at=func.least(sessions.c.expires_at, func.now() + limit))
            .returning(
                sessions.c.expires_at,
                func.extract("epoch", sessions.c.expires_at - func.now()).label("remaining"),
            )
        )
    ).one()
    return IssuedSession(issued.id, issued.token, row.expires_at, max(int(row.remaining), 0))
