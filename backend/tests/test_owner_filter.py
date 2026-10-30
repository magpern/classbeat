"""The repository filters on ``owner_id`` itself, with row-level security out of the picture
(ADR 0002 section 2: RLS is the second wall, not the only one; issue #79).

Every other isolation test runs as the app role, where RLS hides the other user's row, so a
repository query that lost its ``owner_id == ctx.user_id`` would still pass. Here the repository
runs on a session factory of the *superuser* (which bypasses RLS even on forced tables), with two
users' connections in the table: only the application's own filter stands between the calls and
the other user's row.
"""

from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest
from sqlalchemy import Select, event, select, text
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine
from sqlalchemy.orm import ORMExecuteState, Session

from authtools import Env
from classbeat import connections
from classbeat.connections import rewrap as rewrap_module
from classbeat.connections.tables import connections as connections_table
from classbeat.db import create_session_factory, user_session
from ctx import make_context
from keystools import KEY_2, ring_for, rotate, settings_for

ALICE_TOKEN = "a11ce111-1111-4111-8111-11111111a002"
BOB_TOKEN = "b0b00111-1111-4111-8111-11111111b002"


class Pair:
    def __init__(self, env: Env, engine: AsyncEngine, bypass: Any, alice: Any, bob: Any) -> None:
        self.env, self.engine, self.bypass, self.alice, self.bob = env, engine, bypass, alice, bob
        self.alice_id: UUID
        self.bob_id: UUID

    async def rows(self) -> dict[UUID, tuple[str, str | None]]:
        """(owner -> status, last_error) of every row, read around RLS."""
        async with user_session(self.bypass, self.alice) as db:
            found = await db.execute(text("SELECT owner_id, status, last_error FROM connections"))
            return {r.owner_id: (r.status, r.last_error) for r in found}

    async def stored(self, owner: UUID) -> tuple[Any, ...]:
        """Everything a write could change on ``owner``'s row, read around RLS."""
        async with user_session(self.bypass, self.alice) as db:
            row = (
                await db.execute(
                    text(
                        "SELECT status, last_error, secret, secret_nonce, wrapped_dek, dek_nonce, "
                        "key_id, generation, updated_at FROM connections WHERE owner_id = :o"
                    ),
                    {"o": owner},
                )
            ).one()
            return tuple(bytes(v) if isinstance(v, memoryview) else v for v in row)

    def redirected(self, target: UUID) -> async_sessionmaker[Any]:
        """A bypass factory whose locking read of ``connections`` (``SELECT ... FOR UPDATE``)
        finds the row ``target`` whatever its owner: the read as if it had lost its own owner
        filter. What the write after it does with that row is then up to the write's filter."""

        class Redirecting(Session):
            pass

        def swap(state: ORMExecuteState) -> None:
            statement = state.statement
            if (
                isinstance(statement, Select)
                and statement._for_update_arg is not None  # pyright: ignore[reportPrivateUsage]
                and connections_table in statement.get_final_froms()
            ):
                columns = list(statement.selected_columns)
                state.statement = (
                    select(*columns).where(connections_table.c.id == target).with_for_update()
                )

        event.listen(Redirecting, "do_orm_execute", swap)
        return async_sessionmaker(
            self.engine, expire_on_commit=False, sync_session_class=Redirecting
        )


@pytest.fixture
async def pair(env: Env, database_url: str) -> AsyncIterator[Pair]:
    alice = make_context(await env.user("alice@example.com"))
    bob = make_context(await env.user("bob@example.com"))
    ring = env.app.state.keyring
    await connections.save(env.factory, ring, alice, "mowl", {"token": ALICE_TOKEN})
    await connections.save(env.factory, ring, bob, "mowl", {"token": BOB_TOKEN})
    engine = create_async_engine(database_url)
    bypass: async_sessionmaker[Any] = create_session_factory(engine)
    both = Pair(env, engine, bypass, alice, bob)
    try:
        # Self-check of the setup: with this factory RLS really is out of the way.
        assert len(await both.rows()) == 2
        async with user_session(env.factory, alice) as db:
            assert (await db.execute(text("SELECT count(*) FROM connections"))).scalar_one() == 1
        both.alice_id = (await connections.list_connections(env.factory, alice))[0].id
        both.bob_id = (await connections.list_connections(env.factory, bob))[0].id
        yield both
    finally:
        await engine.dispose()


async def test_list_and_refs_return_only_the_callers_row(pair: Pair) -> None:
    listed = await connections.list_connections(pair.bypass, pair.alice)
    assert [c.id for c in listed] == [pair.alice_id]
    refs = await connections.connection_refs(pair.bypass, pair.alice)
    assert refs == {("mowl", pair.alice_id)}


async def test_find_does_not_return_another_users_row(pair: Pair) -> None:
    assert await connections.find(pair.bypass, pair.alice, pair.bob_id) is None
    found = await connections.find(pair.bypass, pair.alice, pair.alice_id)
    assert found is not None and found.id == pair.alice_id  # noqa: PT018


async def test_open_decrypts_only_the_callers_row(pair: Pair) -> None:
    ring = pair.env.app.state.keyring
    opened = await connections.open_connection(pair.bypass, ring, pair.alice, "mowl")
    assert opened.credentials.mowl_token == ALICE_TOKEN
    assert opened.id == pair.alice_id


async def test_open_works_for_the_user_whose_row_is_not_the_first_in_the_table(pair: Pair) -> None:
    """Bob's row was written second: without the filter a ``.first()`` would pick Alice's."""
    ring = pair.env.app.state.keyring
    opened = await connections.open_connection(pair.bypass, ring, pair.bob, "mowl")
    assert opened.credentials.mowl_token == BOB_TOKEN
    assert opened.id == pair.bob_id


async def test_save_replaces_only_the_callers_row(pair: Pair) -> None:
    ring = pair.env.app.state.keyring
    new_token = "c0ffee11-1111-4111-8111-11111111c0de"
    saved = await connections.save(pair.bypass, ring, pair.bob, "mowl", {"token": new_token})
    assert saved == pair.bob_id  # his own row is reused in place
    bobs = await connections.open_connection(pair.bypass, ring, pair.bob, "mowl")
    alices = await connections.open_connection(pair.bypass, ring, pair.alice, "mowl")
    assert (bobs.credentials.mowl_token, alices.credentials.mowl_token) == (new_token, ALICE_TOKEN)


async def test_disconnect_deletes_only_the_callers_row(pair: Pair) -> None:
    assert await connections.disconnect(pair.bypass, pair.alice, "mowl") is True
    assert list(await pair.rows()) == [pair.bob.user_id]


async def test_status_changes_touch_only_the_callers_row(pair: Pair) -> None:
    """Alice's context with Bob's opened connection (his id and version): nothing changes."""
    ring = pair.env.app.state.keyring
    bobs = await connections.open_connection(pair.env.factory, ring, pair.bob, "mowl")
    assert await connections.mark_needs_reconnect(pair.bypass, pair.alice, bobs) is False
    assert await connections.record_result(pair.bypass, pair.alice, bobs, "rate_limited") is False
    async with user_session(pair.bypass, pair.alice) as db:
        assert await connections.hold(db, pair.alice, bobs) is False
    assert (await pair.rows())[pair.bob.user_id] == ("connected", None)
    # The same calls with his own context do change his row (the filter is not just always false).
    assert await connections.mark_needs_reconnect(pair.bypass, pair.bob, bobs) is True
    assert (await pair.rows())[pair.bob.user_id] == ("needs_reconnect", "needs_reconnect")
    assert (await pair.rows())[pair.alice.user_id] == ("connected", None)


async def test_delete_all_in_deletes_only_the_callers_rows(pair: Pair) -> None:
    async with user_session(pair.bypass, pair.alice) as db:
        assert await connections.delete_all_in(db, pair.alice) == ["mowl"]
    assert list(await pair.rows()) == [pair.bob.user_id]


# --- writes whose own read already filters (#139 item 1): the write's filter is the second wall ---


async def test_the_save_update_alone_keeps_another_users_row(pair: Pair) -> None:
    """``save``'s update filters on the owner as well as on the id its locking read returned. With
    that read redirected to Bob's row (as if it had lost its filter), Alice's reconnect must
    write nothing to it."""
    ring = pair.env.app.state.keyring
    before = await pair.stored(pair.bob.user_id)
    alices = await pair.stored(pair.alice.user_id)
    redirected = pair.redirected(pair.bob_id)
    token = "c0ffee11-1111-4111-8111-11111111c0de"
    await connections.save(redirected, ring, pair.alice, "mowl", {"token": token})
    assert await pair.stored(pair.bob.user_id) == before
    assert await pair.stored(pair.alice.user_id) == alices
    bobs = await connections.open_connection(pair.bypass, ring, pair.bob, "mowl")
    assert bobs.credentials.mowl_token == BOB_TOKEN


# --- keys rewrap (#139 item 1) --------------------------------------------------------------


@pytest.fixture
async def rotated(pair: Pair, app_database_url: str, kek_dir: Path) -> AsyncIterator[Any]:
    """Both rows under ``kek-1``, which is now retired: ``kek-2`` is current."""
    rotate(kek_dir, "kek-2", KEY_2, old_id="kek-1")
    ring, engine = await ring_for(settings_for(app_database_url, "kek-2"))
    try:
        yield ring
    finally:
        await engine.dispose()


async def test_rows_by_key_counts_only_the_callers_rows(pair: Pair) -> None:
    assert await rewrap_module.rows_by_key(pair.bypass, pair.alice) == {"kek-1": 1}


async def test_rewrap_owner_moves_only_the_callers_row(pair: Pair, rotated: Any) -> None:
    bobs = await pair.stored(pair.bob.user_id)
    counts = await rewrap_module.rewrap_owner(pair.bypass, rotated, pair.alice)
    assert counts == {"kek-1 rewrapped": 1}  # Bob's row is never even tried ("- skipped")
    assert await pair.stored(pair.bob.user_id) == bobs
    assert (await pair.stored(pair.alice.user_id))[6] == "kek-2"


async def test_rewrapping_another_users_row_id_finds_nothing(pair: Pair, rotated: Any) -> None:
    bobs = await pair.stored(pair.bob.user_id)
    rewrap_row = rewrap_module._rewrap_row  # pyright: ignore[reportPrivateUsage]
    assert await rewrap_row(pair.bypass, rotated, pair.alice, pair.bob_id) == ("-", "skipped")
    assert await pair.stored(pair.bob.user_id) == bobs


async def test_the_rewrap_flip_alone_keeps_another_users_row(pair: Pair, rotated: Any) -> None:
    """The read redirected to Bob's row: unwrapping it under Alice's AAD fails, and the
    ``decrypt_failed`` flip must not reach his row."""
    bobs = await pair.stored(pair.bob.user_id)
    rewrap_row = rewrap_module._rewrap_row  # pyright: ignore[reportPrivateUsage]
    redirected = pair.redirected(pair.bob_id)
    outcome = await rewrap_row(redirected, rotated, pair.alice, pair.bob_id)
    assert outcome == ("kek-1", "decrypt_failed")
    assert await pair.stored(pair.bob.user_id) == bobs  # still connected, still kek-1


async def test_the_rewrap_update_alone_keeps_another_users_row(
    pair: Pair, rotated: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The read redirected to Bob's row and the unwrap made to succeed (the AAD binds the owner,
    so only a stub gets this far): the new wrap must not be written to his row."""
    bobs = await pair.stored(pair.bob.user_id)
    monkeypatch.setattr(
        rewrap_module, "rewrap", lambda wrapped, nonce, *_: (bytes(wrapped)[::-1], bytes(nonce))
    )
    rewrap_row = rewrap_module._rewrap_row  # pyright: ignore[reportPrivateUsage]
    redirected = pair.redirected(pair.bob_id)
    outcome = await rewrap_row(redirected, rotated, pair.alice, pair.bob_id)
    assert outcome == ("kek-1", "skipped")
    assert await pair.stored(pair.bob.user_id) == bobs
