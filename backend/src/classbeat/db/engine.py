"""Database engine and session factory. Nothing here connects or runs at import time."""

import asyncio
import sys
from collections.abc import Callable
from typing import Any

from sqlalchemy import event
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from classbeat.config import Settings
from classbeat.db.role_check import refuse_privileged_roles

CONNECT_TIMEOUT_SECONDS = 5


def discard_session_state(engine: AsyncEngine) -> None:
    """Run ``DISCARD ALL`` whenever a connection goes back to the pool.

    A plain rollback leaves ``WITH HOLD`` cursors, temp tables, prepared statements, advisory
    locks and session settings on the connection, and the next user of the pooled connection
    would inherit them (ADR 0002 section 5; a held cursor opened as user A can still be fetched
    from as user B). The engine is built with ``pool_reset_on_return=None`` so this handler is
    the whole reset: it rolls back first (``DISCARD ALL`` cannot run in a transaction block),
    then discards. If either step fails the pool invalidates the connection, so it is closed and
    never reused. psycopg clears its own prepared-statement cache when it sees ``DISCARD ALL``.
    """

    @event.listens_for(engine.sync_engine, "reset")
    def discard(dbapi_connection: Any, _record: object, reset_state: Any) -> None:
        if reset_state.terminate_only or not reset_state.asyncio_safe:
            return  # the connection is being closed, not reused
        dbapi_connection.rollback()
        dbapi_connection.autocommit = True
        try:
            cursor = dbapi_connection.cursor()
            try:
                cursor.execute("DISCARD ALL")
            finally:
                cursor.close()
        finally:
            dbapi_connection.autocommit = False


def create_engine(settings: Settings, **kwargs: Any) -> AsyncEngine:
    """Build the app role's async engine (web and worker). It connects lazily, on first use.

    Every new connection is checked: a role that is a superuser, has BYPASSRLS or owns tables
    is refused (ADR 0002 section 4). Every connection returned to the pool is wiped with
    ``DISCARD ALL``.
    """
    kwargs["pool_reset_on_return"] = None  # discard_session_state does the rollback itself
    engine = create_async_engine(
        settings.sqlalchemy_url(),
        pool_pre_ping=True,
        connect_args={"connect_timeout": CONNECT_TIMEOUT_SECONDS},
        **kwargs,
    )
    refuse_privileged_roles(engine)
    discard_session_state(engine)
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
