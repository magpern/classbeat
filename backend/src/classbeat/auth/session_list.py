"""The user's own session list (ADR 0003 section 3): what the account page shows of each live
session. Never the token or its hash."""

from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from classbeat.auth.tables import sessions
from classbeat.config import Settings


@dataclass(frozen=True, slots=True)
class SessionSummary:
    id: UUID
    created_at: datetime
    last_seen_at: datetime
    expires_at: datetime
    user_agent: str | None
    ip: str | None
    location_changed_at: datetime | None  # seen from a new network or browser since (#632)


async def list_sessions(
    db: AsyncSession, settings: Settings, user_id: UUID
) -> list[SessionSummary]:
    """The user's own live sessions, newest first. Never the token or its hash.

    Absolute- and idle-expired rows that the purge has not removed yet are left out, the same
    rule as ``resolve_session`` (a row whose ``expires_at`` has passed, or whose ``last_seen_at``
    is ``session_idle_days`` or more ago, is not a session).
    """
    idle = timedelta(days=settings.session_idle_days)
    rows = await db.execute(
        select(
            sessions.c.id,
            sessions.c.created_at,
            sessions.c.last_seen_at,
            sessions.c.expires_at,
            sessions.c.user_agent,
            sessions.c.ip,
            sessions.c.location_changed_at,
        )
        .where(
            sessions.c.user_id == user_id,
            sessions.c.expires_at > func.now(),
            sessions.c.last_seen_at > func.now() - idle,
        )
        .order_by(sessions.c.created_at.desc())
    )
    return [SessionSummary(*row) for row in rows.all()]
