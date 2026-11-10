"""The worker's database session for ``suite --live --record`` (ADR 0012 section 5).

Opened only when recording, with the app role (``verify_database_role``): the one transaction
``suite_live.record`` writes the results in. Split out of ``suite_live``, which re-exports
``SessionOpener`` and ``worker_session``.
"""

from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager

from sqlalchemy.ext.asyncio import AsyncSession

from classbeat.config import Settings

SessionOpener = Callable[[], AbstractAsyncContextManager[AsyncSession]]


@asynccontextmanager
async def _worker_session(settings: Settings) -> AsyncIterator[AsyncSession]:
    from classbeat.db import (
        create_engine,
        create_session_factory,
        system_session,
        verify_database_role,
    )

    engine = create_engine(settings)
    try:
        await verify_database_role(engine)
        async with system_session(create_session_factory(engine)) as db:
            yield db
    finally:
        await engine.dispose()


def worker_session(settings: Settings) -> SessionOpener:
    """The worker's database transaction (the app role), opened only when recording."""
    return lambda: _worker_session(settings)
