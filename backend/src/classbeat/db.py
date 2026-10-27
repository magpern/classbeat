"""Database engine and session factory. Nothing here connects or runs at import time."""

import asyncio
import sys
from collections.abc import Callable
from typing import Any

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from classbeat.config import Settings

CONNECT_TIMEOUT_SECONDS = 5


def create_engine(settings: Settings, **kwargs: Any) -> AsyncEngine:
    """Build the async engine. It connects lazily, on first use."""
    return create_async_engine(
        settings.sqlalchemy_url(),
        pool_pre_ping=True,
        connect_args={"connect_timeout": CONNECT_TIMEOUT_SECONDS},
        **kwargs,
    )


def create_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False)


def event_loop_factory() -> Callable[[], asyncio.AbstractEventLoop] | None:
    """For ``asyncio.run(loop_factory=...)``: psycopg async needs a selector loop on Windows."""
    return asyncio.SelectorEventLoop if sys.platform == "win32" else None
