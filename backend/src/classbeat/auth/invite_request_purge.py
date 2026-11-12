"""The retention of invite requests (ADR 0033 section 6), for the hourly purge
(``classbeat.auth.purge``, run by the worker): a decided request goes 30 days after the decision,
a pending one 90 days after it was made. Kept apart from the web route so the worker loads no web
module (``tests/test_egress_guard.py``).
"""

from datetime import timedelta

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from classbeat.auth.invite_request_policy import DECIDED_RETENTION_DAYS, PENDING_RETENTION_DAYS
from classbeat.db import system_session

_PURGE = text(
    "DELETE FROM invite_requests WHERE (state <> 'pending' AND decided_at < now() - :decided) "
    "OR (state = 'pending' AND created_at < now() - :pending)"
)


async def purge_invite_requests(sessions: async_sessionmaker[AsyncSession]) -> int:
    """Delete decided requests 30 days after the decision and pending ones after 90 days."""
    async with system_session(sessions) as db:
        result = await db.execute(
            _PURGE,
            {
                "decided": timedelta(days=DECIDED_RETENTION_DAYS),
                "pending": timedelta(days=PENDING_RETENTION_DAYS),
            },
        )
    return int(result.rowcount or 0)  # pyright: ignore[reportAttributeAccessIssue]
