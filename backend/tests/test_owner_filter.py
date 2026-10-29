"""The repository filters on ``owner_id`` itself, with row-level security out of the picture
(ADR 0002 section 2: RLS is the second wall, not the only one; issue #79).

Every other isolation test runs as the app role, where RLS hides the other user's row, so a
repository query that lost its ``owner_id == ctx.user_id`` would still pass. Here the repository
runs on a session factory of the *superuser* (which bypasses RLS even on forced tables), with two
users' connections in the table: only the application's own filter stands between the calls and
the other user's row.
"""

from collections.abc import AsyncIterator
from typing import Any
from uuid import UUID

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from authtools import Env
from classbeat import connections
from classbeat.db import create_session_factory, user_session
from ctx import make_context

ALICE_TOKEN = "a11ce111-1111-4111-8111-11111111a002"
BOB_TOKEN = "b0b00111-1111-4111-8111-11111111b002"


class Pair:
    def __init__(self, env: Env, bypass: Any, alice: Any, bob: Any) -> None:
        self.env, self.bypass, self.alice, self.bob = env, bypass, alice, bob
        self.alice_id: UUID
        self.bob_id: UUID

    async def rows(self) -> dict[UUID, tuple[str, str | None]]:
        """(owner -> status, last_error) of every row, read around RLS."""
        async with user_session(self.bypass, self.alice) as db:
            found = await db.execute(text("SELECT owner_id, status, last_error FROM connections"))
            return {r.owner_id: (r.status, r.last_error) for r in found}


@pytest.fixture
async def pair(env: Env, database_url: str) -> AsyncIterator[Pair]:
    alice = make_context(await env.user("alice@example.com"))
    bob = make_context(await env.user("bob@example.com"))
    ring = env.app.state.keyring
    await connections.save(env.factory, ring, alice, "mowl", {"token": ALICE_TOKEN})
    await connections.save(env.factory, ring, bob, "mowl", {"token": BOB_TOKEN})
    engine = create_async_engine(database_url)
    bypass: async_sessionmaker[Any] = create_session_factory(engine)
    both = Pair(env, bypass, alice, bob)
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
