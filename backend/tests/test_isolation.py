"""Guards 3 and 4 of ADR 0002 section 6, on test-only owned tables, as the app role."""

import uuid
from collections.abc import AsyncIterator

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError, ProgrammingError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.pool import AsyncAdaptedQueuePool

from classbeat.config import Settings
from classbeat.context import UserContext
from classbeat.db import (
    TransactionOwnedError,
    create_engine,
    create_migrations_engine,
    create_session_factory,
    system_session,
    user_session,
)
from ctx import make_context

pytestmark = pytest.mark.usefixtures("owned_tables")

A = make_context()
B = make_context()
Sessions = async_sessionmaker[AsyncSession]
INSERT_PARENT = text("INSERT INTO t_parent (owner_id, name) VALUES (:o, :n) RETURNING id")
INSERT_CHILD = text("INSERT INTO t_child (owner_id, parent_id) VALUES (:o, :p)")


def settings_for(app_url: str) -> Settings:
    return Settings(database_url=app_url, environment="test")  # pyright: ignore[reportArgumentType]


@pytest.fixture
async def sessions(app_database_url: str) -> AsyncIterator[Sessions]:
    engine = create_engine(settings_for(app_database_url))
    try:
        yield create_session_factory(engine)
    finally:
        await engine.dispose()


async def add_parent(sessions: Sessions, ctx: UserContext, name: str) -> uuid.UUID:
    async with user_session(sessions, ctx) as session:
        result = await session.execute(INSERT_PARENT, {"o": ctx.user_id, "n": name})
        return result.scalar_one()


async def names(sessions: Sessions, ctx: UserContext) -> list[str]:
    async with user_session(sessions, ctx) as session:
        return sorted((await session.execute(text("SELECT name FROM t_parent"))).scalars())


async def test_a_never_sees_bs_rows(sessions: Sessions) -> None:
    await add_parent(sessions, A, "a1")
    await add_parent(sessions, B, "b1")
    assert await names(sessions, A) == ["a1"]
    assert await names(sessions, B) == ["b1"]


async def test_a_cannot_update_or_delete_bs_rows(sessions: Sessions) -> None:
    b_id = await add_parent(sessions, B, "b1")
    async with user_session(sessions, A) as session:
        updated = await session.execute(
            text("UPDATE t_parent SET name = 'x' WHERE id = :i"), {"i": b_id}
        )
        deleted = await session.execute(text("DELETE FROM t_parent WHERE id = :i"), {"i": b_id})
        assert (updated.rowcount, deleted.rowcount) == (0, 0)  # pyright: ignore[reportAttributeAccessIssue]
    assert await names(sessions, B) == ["b1"]


async def test_a_cannot_insert_a_row_for_b(sessions: Sessions) -> None:
    with pytest.raises(DBAPIError, match="row-level security"):
        async with user_session(sessions, A) as session:
            await session.execute(INSERT_PARENT, {"o": B.user_id, "n": "x"})
    assert await names(sessions, B) == []


async def test_a_cannot_move_a_row_to_b(sessions: Sessions) -> None:
    await add_parent(sessions, A, "a1")
    with pytest.raises(DBAPIError, match="row-level security"):
        async with user_session(sessions, A) as session:
            await session.execute(text("UPDATE t_parent SET owner_id = :o"), {"o": B.user_id})
    assert await names(sessions, A) == ["a1"]


async def test_a_cannot_create_a_child_of_bs_parent(sessions: Sessions) -> None:
    b_parent = await add_parent(sessions, B, "b1")
    first_lines = []
    for parent_id in (b_parent, uuid.uuid4()):  # B's real parent, and one that does not exist
        with pytest.raises(IntegrityError) as excinfo:
            async with user_session(sessions, A) as session:
                await session.execute(INSERT_CHILD, {"o": A.user_id, "p": parent_id})
        first_lines.append(str(excinfo.value.orig).splitlines()[0])
    # A cannot tell B's ID from a made-up one: the same constraint error either way.
    assert "t_child_owner_id_parent_id_fkey" in first_lines[0]
    assert first_lines[0] == first_lines[1]
    # With B's owner_id on the child, RLS refuses the row.
    with pytest.raises(DBAPIError, match="row-level security"):
        async with user_session(sessions, A) as session:
            await session.execute(INSERT_CHILD, {"o": B.user_id, "p": b_parent})


async def test_a_child_of_its_own_parent_works_and_is_private(sessions: Sessions) -> None:
    a_parent = await add_parent(sessions, A, "a1")
    async with user_session(sessions, A) as session:
        await session.execute(INSERT_CHILD, {"o": A.user_id, "p": a_parent})
    async with user_session(sessions, B) as session:
        assert (await session.execute(text("SELECT count(*) FROM t_child"))).scalar_one() == 0


async def test_a_session_without_a_context_sees_no_rows(sessions: Sessions) -> None:
    await add_parent(sessions, A, "a1")
    async with system_session(sessions) as session:
        assert (await session.execute(text("SELECT count(*) FROM t_parent"))).scalar_one() == 0
    async with sessions() as session:  # the helper was never used on this connection
        assert (await session.execute(text("SELECT count(*) FROM t_parent"))).scalar_one() == 0
    with pytest.raises(DBAPIError, match="row-level security"):
        async with system_session(sessions) as session:
            await session.execute(INSERT_PARENT, {"o": A.user_id, "n": "x"})


async def test_a_unique_value_of_b_cannot_be_detected_or_blocked_by_a(sessions: Sessions) -> None:
    await add_parent(sessions, B, "taken")
    await add_parent(sessions, A, "taken")  # not blocked, so A learns nothing about B
    assert await names(sessions, A) == ["taken"]


@pytest.mark.parametrize(
    "statement",
    [
        "ALTER TABLE t_parent DISABLE ROW LEVEL SECURITY",
        "ALTER TABLE t_parent NO FORCE ROW LEVEL SECURITY",
        "ALTER TABLE t_parent OWNER TO classbeat_app",
        "DROP POLICY owner_isolation ON t_parent",
        "CREATE POLICY open ON t_parent USING (true)",
        "ALTER TABLE t_parent ADD COLUMN x int",
        "DROP TABLE t_parent",
        "TRUNCATE t_parent",
        "CREATE TABLE t_new (id int)",
        "CREATE TEMP TABLE t_tmp (id int)",
        "SET ROLE classbeat_owner",
        "CREATE SCHEMA s",
        "CREATE FUNCTION f() RETURNS int LANGUAGE sql AS 'SELECT 1'",
    ],
)
async def test_the_app_role_cannot_change_the_schema(sessions: Sessions, statement: str) -> None:
    with pytest.raises(ProgrammingError, match=r"permission denied|must be owner"):
        async with user_session(sessions, A) as session:
            await session.execute(text(statement))


async def test_the_owner_is_also_bound_by_the_policy(owner_database_url: str) -> None:
    settings = Settings(migrations_database_url=owner_database_url, environment="test")  # pyright: ignore[reportArgumentType]
    engine = create_migrations_engine(settings)
    try:
        async with engine.begin() as connection:
            await connection.execute(
                text("SELECT set_config('app.user_id', :u, true)"), {"u": str(A.user_id)}
            )
            await connection.execute(INSERT_PARENT, {"o": A.user_id, "n": "x"})
        async with engine.begin() as connection:  # FORCE: no setting, no rows, even the owner
            count = (await connection.execute(text("SELECT count(*) FROM t_parent"))).scalar_one()
            assert count == 0
    finally:
        await engine.dispose()


async def test_a_pooled_connection_carries_no_user_id_afterwards(app_database_url: str) -> None:
    engine = create_engine(
        settings_for(app_database_url), poolclass=AsyncAdaptedQueuePool, pool_size=1, max_overflow=0
    )
    sessions = create_session_factory(engine)
    try:
        await add_parent(sessions, A, "a1")
        async with sessions() as session:
            pid = (await session.execute(text("SELECT pg_backend_pid()"))).scalar_one()
            setting = (
                await session.execute(text("SELECT current_setting('app.user_id', true)"))
            ).scalar_one()
            count = (await session.execute(text("SELECT count(*) FROM t_parent"))).scalar_one()
        async with user_session(sessions, A) as session:
            same = (await session.execute(text("SELECT pg_backend_pid()"))).scalar_one() == pid
        assert same, "the test must reuse the same connection"
        assert setting in (None, "")
        assert count == 0
    finally:
        await engine.dispose()


async def test_commit_and_rollback_inside_a_session_raise(sessions: Sessions) -> None:
    for manager in (user_session(sessions, A), system_session(sessions)):
        async with manager as session:
            with pytest.raises(TransactionOwnedError):
                await session.commit()
            with pytest.raises(TransactionOwnedError):
                await session.rollback()
            with pytest.raises(TransactionOwnedError):
                await session.run_sync(lambda sync_session: sync_session.commit())
    # The refusals leave the transaction intact: it still works and commits at the end.
    a_id = await add_parent(sessions, A, "kept")
    async with user_session(sessions, A) as session:
        with pytest.raises(TransactionOwnedError):
            await session.commit()
        await session.execute(
            text("UPDATE t_parent SET name = 'changed' WHERE id = :i"), {"i": a_id}
        )
    assert await names(sessions, A) == ["changed"]


async def test_an_exception_rolls_back_the_whole_session(sessions: Sessions) -> None:
    async def failing() -> None:
        async with user_session(sessions, A) as session:
            await session.execute(INSERT_PARENT, {"o": A.user_id, "n": "x"})
            raise ValueError("boom")

    with pytest.raises(ValueError, match="boom"):
        await failing()
    assert await names(sessions, A) == []


async def test_a_raw_user_id_is_not_a_context(sessions: Sessions) -> None:
    with pytest.raises(TypeError, match="UserContext"):
        async with user_session(sessions, A.user_id):  # pyright: ignore[reportArgumentType]
            pytest.fail("a raw user id must not open a session")


async def test_savepoints_work_and_keep_the_user_id(sessions: Sessions) -> None:
    async with user_session(sessions, A) as session:
        await session.execute(INSERT_PARENT, {"o": A.user_id, "n": "kept"})
        with pytest.raises(IntegrityError):  # the "insert, catch the unique violation" pattern
            async with session.begin_nested():
                await session.execute(INSERT_PARENT, {"o": A.user_id, "n": "kept"})
        user = (await session.execute(text("SELECT current_setting('app.user_id')"))).scalar_one()
        assert user == str(A.user_id)  # survived the rolled-back savepoint
        async with session.begin_nested():  # a savepoint that ends normally is fine too
            await session.execute(INSERT_PARENT, {"o": A.user_id, "n": "second"})
        with pytest.raises(TransactionOwnedError):  # but the transaction is still not ours to end
            await session.commit()
        assert (await session.execute(text("SELECT count(*) FROM t_parent"))).scalar_one() == 2
    assert await names(sessions, A) == ["kept", "second"]


async def test_rolling_back_through_another_path_fails_closed(sessions: Sessions) -> None:
    async with user_session(sessions, A) as session:
        await session.execute(INSERT_PARENT, {"o": A.user_id, "n": "x"})
        await session.run_sync(lambda sync_session: sync_session.rollback())
        # The transaction (and with it app.user_id) is gone: no rows, and no owned writes.
        assert (await session.execute(text("SELECT count(*) FROM t_parent"))).scalar_one() == 0
        with pytest.raises(DBAPIError, match="row-level security"):
            await session.execute(INSERT_PARENT, {"o": A.user_id, "n": "y"})
