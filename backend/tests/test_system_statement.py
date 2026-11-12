"""``db.system_statement`` (#998): one system-table write as its own implicit transaction.

It takes a pooled connection in autocommit, so the pool's reset must hand that connection back in
normal transaction mode: a later ``user_session`` on the same connection must still run in one
transaction, or its transaction-local ``app.user_id`` would be gone after its first statement.
"""

import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime

import pytest
from sqlalchemy import Column, Integer, MetaData, Table, select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.pool import AsyncAdaptedQueuePool

from classbeat.auth.tables import rate_limits
from classbeat.config import Settings
from classbeat.db import create_engine, create_session_factory, system_statement, user_session
from classbeat.db.tables import SYSTEM_TABLES
from ctx import make_context

Sessions = async_sessionmaker[AsyncSession]
WINDOW = datetime(2026, 1, 1, tzinfo=UTC)


@pytest.fixture
async def one_connection(clean_auth: None, app_database_url: str) -> AsyncIterator[Sessions]:
    """A pool of exactly one connection, so every use below reuses it."""
    settings = Settings(database_url=app_database_url, environment="test")  # pyright: ignore[reportArgumentType]
    engine = create_engine(
        settings, check_queue=False, poolclass=AsyncAdaptedQueuePool, pool_size=1, max_overflow=0
    )
    try:
        yield create_session_factory(engine)
    finally:
        await engine.dispose()


def upsert(bucket: bytes) -> object:
    return (
        insert(rate_limits)
        .values(bucket_hash=bucket, window_start=WINDOW, count=1)
        .on_conflict_do_update(
            index_elements=[rate_limits.c.bucket_hash, rate_limits.c.window_start],
            set_={"count": rate_limits.c.count + 1},
        )
        .returning(rate_limits.c.count)
    )


async def test_it_commits_and_returns_the_rows(one_connection: Sessions) -> None:
    for expected in (1, 2, 3):
        [row] = await system_statement(one_connection, upsert(b"b"))  # type: ignore[arg-type]
        assert row.count == expected
    # Committed: a fresh transaction sees it.
    async with one_connection() as session:
        assert (await session.execute(select(rate_limits.c.count))).scalar_one() == 3


async def test_a_statement_without_returning_gives_no_rows(one_connection: Sessions) -> None:
    statement = insert(rate_limits).values(bucket_hash=b"c", window_start=WINDOW, count=0)
    assert await system_statement(one_connection, statement) == []


async def test_the_connection_comes_back_in_transaction_mode(one_connection: Sessions) -> None:
    """After an autocommit statement the same connection runs a ``user_session`` as one
    transaction: the user's setting is still there at its second statement, and a rolled-back
    write is gone."""
    ctx = make_context()
    pids: set[int] = set()
    for _ in range(3):
        await system_statement(one_connection, upsert(b"d"))  # type: ignore[arg-type]
        async with user_session(one_connection, ctx) as session:
            pids.add((await session.execute(text("SELECT pg_backend_pid()"))).scalar_one())
            setting = text("SELECT current_setting('app.user_id', true)")
            assert (await session.execute(setting)).scalar_one() == str(ctx.user_id)
            await session.execute(text("SELECT 1"))
            assert (await session.execute(setting)).scalar_one() == str(ctx.user_id)
    assert len(pids) == 1, "the test must reuse the same connection"
    async with one_connection() as session:  # closed without a commit: rolled back
        await session.execute(
            insert(rate_limits).values(bucket_hash=b"e", window_start=WINDOW, count=0)
        )
    async with one_connection() as session:
        found = select(rate_limits.c.count).where(rate_limits.c.bucket_hash == b"e")
        assert (await session.execute(found)).first() is None


async def test_it_leaves_no_user_setting(one_connection: Sessions) -> None:
    async with user_session(one_connection, make_context()):
        pass
    statement = (
        insert(rate_limits)
        .values(bucket_hash=b"f", window_start=WINDOW, count=0)
        .returning(text("current_setting('app.user_id', true) AS who"))
    )
    [row] = await system_statement(one_connection, statement)
    assert row.who in (None, "")


@pytest.mark.parametrize("owned", ["classes", "jobs", "connections"])
def test_it_refuses_an_owned_table(owned: str) -> None:
    assert owned not in SYSTEM_TABLES
    table = Table(owned, MetaData(), Column("n", Integer))
    with pytest.raises(ValueError, match="system table only"):
        asyncio.run(system_statement(None, table.insert().values(n=1)))  # type: ignore[arg-type]


def test_it_refuses_a_statement_with_no_table() -> None:
    with pytest.raises(ValueError, match="system table only"):
        asyncio.run(system_statement(None, text("DELETE FROM sessions")))  # type: ignore[arg-type]
