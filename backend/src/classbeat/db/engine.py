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
from classbeat.db.role_check import refuse_privileged_roles

CONNECT_TIMEOUT_SECONDS = 5


def create_engine(settings: Settings, **kwargs: Any) -> AsyncEngine:
    """Build the app role's async engine (web and worker). It connects lazily, on first use.

    Every new connection is checked: a role that is a superuser, has BYPASSRLS or owns tables
    is refused (ADR 0002 section 4).
    """
    engine = create_async_engine(
        settings.sqlalchemy_url(),
        pool_pre_ping=True,
        connect_args={"connect_timeout": CONNECT_TIMEOUT_SECONDS},
        **kwargs,
    )
    refuse_privileged_roles(engine)
    return engine


def create_migrations_engine(settings: Settings, **kwargs: Any) -> AsyncEngine:
    """The owner role's engine, for Alembic only. Never used by web or worker."""
    return create_async_engine(
        settings.migrations_sqlalchemy_url(),
        connect_args={"connect_timeout": CONNECT_TIMEOUT_SECONDS},
        **kwargs,
    )


def create_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False)


def event_loop_factory() -> Callable[[], asyncio.AbstractEventLoop] | None:
    """For ``asyncio.run(loop_factory=...)``: psycopg async needs a selector loop on Windows."""
    return asyncio.SelectorEventLoop if sys.platform == "win32" else None
