"""The ``spotify_connection`` deletion path (guard 10; ADR 0008 sections 2 and 8, ADR 0009 sections
5, 7 and 9; issue SP1, #174): plant a row in every Spotify-derived place, then each application
deletion removes exactly what ADR 0008 section 8's table says and nothing else, in the caller's
transaction, for its owner only, and without any request to Spotify (the network guard).

- consent revoke (``revoke_pool``), ``pool_missing`` and a Client ID change: the pool operation
  rows (``class_id IS NULL``) go, with the consent row (revoke, Client ID change) or the pool's
  playlist id (``pool_missing``); final operations and class playlists stay;
- Disconnect, another Spotify account and ``keys wipe``: every Spotify operation and the consent
  go, live class playlists are detached;
- the account: everything (cascade).

MOWL data is never touched by these paths.
"""

import uuid
from collections.abc import Iterator
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Connection

from authtools import Env
from classbeat import auth, connections
from classbeat.connections.spotify_data import (
    forget_pool,
    forget_spotify_account,
    pool_missing,
    revoke_pool,
)
from classbeat.db import user_session
from ctx import make_context
from jobtools import digest, engine_for, new_class
from test_classes_schema import insert

CLIENT_A, CLIENT_B = "0123456789abcdef0123456789abcdef", "fedcba9876543210fedcba9876543210"
POOL_PLAYLIST, FINAL_PLAYLIST = "37i9dQZF1DXcBWIGoYBM5M", "0vvXsWCC9xrXsKd4FyS8kM"
SPOTIFY_USER = "user-a"


@pytest.fixture
def admin(database_url: str) -> Iterator[Connection]:
    with (
        engine_for(database_url, isolation_level="AUTOCOMMIT") as engine,
        engine.connect() as connection,
    ):
        yield connection


def tokens(access: str = "access-1") -> dict[str, Any]:
    return {"access_token": access, "refresh_token": "refresh-1", "expires_at": 2_000_000_000}


async def connect(env: Env, ctx: Any, client_id: str = CLIENT_A, user: str = SPOTIFY_USER) -> None:
    await connections.save(
        env.factory, env.app.state.keyring, ctx, "spotify", tokens(),
        public={"client_id": client_id, "spotify_user_id": user},
    )  # fmt: skip


def operation(admin: Connection, owner: uuid.UUID, kind: str, **c: Any) -> uuid.UUID:
    values: dict[str, Any] = {"state": "pending", **c}
    return insert(admin, "external_operations", owner_id=owner, kind=kind,
                  idempotency_key=f"k{uuid.uuid4()}", request_hash=digest("op"), lease_version=1,
                  **values)  # fmt: skip


def plant(admin: Connection, owner: uuid.UUID) -> dict[str, Any]:
    """Every Spotify-derived row of ADR 0008 section 8 for ``owner``, plus a MOWL attach and link
    (which no Spotify path may touch)."""
    class_id = new_class(admin, owner)
    pool_id = insert(admin, "spotify_pool", owner_id=owner, spotify_user_id=SPOTIFY_USER,
                     consent_version=1, playlist_id=POOL_PLAYLIST, last_hash=digest("pool"),
                     last_written_at="2026-10-30")  # fmt: skip
    ids = {
        "class": class_id,
        "pool": pool_id,
        "pool_create": operation(admin, owner, "spotify_create_playlist", state="succeeded",
                                 external_id=POOL_PLAYLIST,
                                 request_context={"purpose": "pool", "ref": "a1b2c3d4e5f6"}),
        "pool_replace": operation(admin, owner, "spotify_replace_items", state="uncertain"),
        "final_create": operation(admin, owner, "spotify_create_playlist", class_id=class_id,
                                  state="succeeded", external_id=FINAL_PLAYLIST),
        "final_fill": operation(admin, owner, "spotify_replace_items", class_id=class_id),
        "attach": operation(admin, owner, "mowl_attach_spotify_playlist", state="succeeded",
                            external_id="7"),
    }  # fmt: skip
    ids["playlist"] = insert(
        admin, "class_playlists", owner_id=owner, class_id=class_id, playlist_id=FINAL_PLAYLIST,
        spotify_user_id=SPOTIFY_USER, name="Tuesday climb", state="verified",
        content_hash=digest("final"), track_count=12, create_operation_id=ids["final_create"],
    )  # fmt: skip
    insert(admin, "mowl_playlist_links", owner_id=owner, source="spotify",
           source_id=POOL_PLAYLIST, playlist_id=7)  # fmt: skip
    return ids


def state(admin: Connection, owner: uuid.UUID) -> dict[str, Any]:
    """What is kept for ``owner``: operation ids by kind, the pool, live and detached playlists."""

    def ids(sql: str) -> set[Any]:
        return set(admin.execute(text(sql), {"o": owner}).scalars().all())

    pool = admin.execute(
        text("SELECT id, playlist_id, last_hash, last_written_at FROM spotify_pool "
             "WHERE owner_id = :o"), {"o": owner},
    ).all()  # fmt: skip
    return {
        "operations": ids("SELECT id FROM external_operations WHERE owner_id = :o"),
        "pool": [tuple(r) for r in pool],
        "live": ids("SELECT id FROM class_playlists WHERE owner_id = :o AND detached_at IS NULL"),
        "detached": ids(
            "SELECT id FROM class_playlists WHERE owner_id = :o AND detached_at IS NOT NULL"
        ),
        "links": ids("SELECT id FROM mowl_playlist_links WHERE owner_id = :o"),
        "connection": ids("SELECT generation FROM connections WHERE owner_id = :o"),
    }


def expect_pool_forgotten(before: dict[str, Any], planted: dict[str, Any]) -> dict[str, Any]:
    return {
        **before,
        "operations": before["operations"] - {planted["pool_create"], planted["pool_replace"]},
    }


async def users(env: Env, admin: Connection) -> tuple[Any, Any, dict[str, Any], dict[str, Any]]:
    """Two users with a Spotify connection and every planted row each."""
    alice = make_context(await env.user("a@example.com"))
    bob = make_context(await env.user("b@example.com"))
    for ctx in (alice, bob):
        await connect(env, ctx)
    return alice, bob, plant(admin, alice.user_id), plant(admin, bob.user_id)


async def test_a_consent_revoke_deletes_the_consent_and_the_pool_operations_only(
    env: Env, admin: Connection
) -> None:
    alice, bob, planted, _ = await users(env, admin)
    before, bob_before = state(admin, alice.user_id), state(admin, bob.user_id)
    async with user_session(env.factory, alice) as db:
        await revoke_pool(db, alice.user_id)
    assert state(admin, alice.user_id) == {**expect_pool_forgotten(before, planted), "pool": []}
    assert state(admin, bob.user_id) == bob_before


async def test_a_crash_after_the_revoke_keeps_both_the_consent_and_the_operations(
    env: Env, admin: Connection
) -> None:
    alice, _, _, _ = await users(env, admin)
    before = state(admin, alice.user_id)

    async def crash() -> None:
        async with user_session(env.factory, alice) as db:
            await forget_pool(db, alice.user_id)
            raise RuntimeError("crash between the statements")

    with pytest.raises(RuntimeError):
        await crash()
    assert state(admin, alice.user_id) == before


async def test_pool_missing_clears_the_playlist_and_forgets_the_pool_operations(
    env: Env, admin: Connection
) -> None:
    alice, bob, planted, _ = await users(env, admin)
    before, bob_before = state(admin, alice.user_id), state(admin, bob.user_id)
    # Not the playlist the row holds (the pool was set up again meanwhile): nothing changes.
    async with user_session(env.factory, alice) as db:
        assert await pool_missing(db, alice.user_id, planted["pool"], FINAL_PLAYLIST) is False
        assert await pool_missing(db, alice.user_id, uuid.uuid4(), POOL_PLAYLIST) is False
    assert state(admin, alice.user_id) == before
    # Bob's pool named under Alice's context: nothing either (owner filter and RLS).
    bob_pool = state(admin, bob.user_id)["pool"][0][0]
    async with user_session(env.factory, alice) as db:
        assert await pool_missing(db, alice.user_id, bob_pool, POOL_PLAYLIST) is False
    async with user_session(env.factory, alice) as db:
        assert await pool_missing(db, alice.user_id, planted["pool"], POOL_PLAYLIST) is True
    assert state(admin, alice.user_id) == {
        **expect_pool_forgotten(before, planted),
        "pool": [(planted["pool"], None, None, None)],  # the consent stays
    }
    assert state(admin, bob.user_id) == bob_before


async def test_a_client_id_change_forgets_the_pool_and_keeps_the_class_playlists(
    env: Env, admin: Connection
) -> None:
    alice, bob, planted, _ = await users(env, admin)
    before, bob_before = state(admin, alice.user_id), state(admin, bob.user_id)
    await connect(env, alice, client_id=CLIENT_B)
    assert state(admin, alice.user_id) == {
        **expect_pool_forgotten(before, planted), "pool": [], "connection": {2},
    }  # fmt: skip
    assert state(admin, bob.user_id) == bob_before


async def test_a_reconnect_of_the_same_account_and_app_keeps_everything(
    env: Env, admin: Connection
) -> None:
    alice, _, _, _ = await users(env, admin)
    before = state(admin, alice.user_id)
    await connect(env, alice)
    assert state(admin, alice.user_id) == {**before, "connection": {2}}


async def test_another_spotify_account_forgets_everything_kept_about_the_old_one(
    env: Env, admin: Connection
) -> None:
    alice, bob, planted, _ = await users(env, admin)
    before, bob_before = state(admin, alice.user_id), state(admin, bob.user_id)
    await connect(env, alice, user="user-other")
    assert state(admin, alice.user_id) == {
        **before,
        "operations": {planted["attach"]},
        "pool": [],
        "live": set(),
        "detached": {planted["playlist"]},
        "connection": {2},
    }
    assert state(admin, bob.user_id) == bob_before


async def test_disconnect_forgets_the_account_and_deletes_the_row(
    env: Env, admin: Connection
) -> None:
    alice, bob, planted, _ = await users(env, admin)
    before, bob_before = state(admin, alice.user_id), state(admin, bob.user_id)
    assert await connections.disconnect(env.factory, alice, "spotify") is True
    assert state(admin, alice.user_id) == {
        **before,
        "operations": {planted["attach"]},
        "pool": [],
        "live": set(),
        "detached": {planted["playlist"]},
        "connection": set(),
    }
    assert state(admin, bob.user_id) == bob_before
    # The detached playlist then goes with its class.
    admin.execute(text("DELETE FROM classes WHERE id = :c"), {"c": planted["class"]})
    assert state(admin, alice.user_id)["detached"] == set()


async def test_keys_wipe_forgets_the_spotify_account_too(env: Env, admin: Connection) -> None:
    alice, bob, planted, _ = await users(env, admin)
    bob_before = state(admin, bob.user_id)
    async with user_session(env.factory, alice) as db:
        assert await connections.delete_all_in(db, alice) == ["spotify"]
    after = state(admin, alice.user_id)
    assert (after["operations"], after["pool"], after["live"]) == (set(), [], set())
    assert after["detached"] == {planted["playlist"]}
    assert state(admin, bob.user_id) == bob_before


async def test_forget_spotify_account_without_a_connection_is_a_no_op_elsewhere(
    env: Env, admin: Connection
) -> None:
    alice, bob, planted, _ = await users(env, admin)
    bob_before = state(admin, bob.user_id)
    async with user_session(env.factory, alice) as db:
        await forget_spotify_account(db, bob.user_id)  # another owner's id: RLS hides it
    assert state(admin, bob.user_id) == bob_before
    assert planted["pool"] in {r[0] for r in state(admin, alice.user_id)["pool"]}


async def test_the_account_deletion_removes_every_spotify_row(env: Env, admin: Connection) -> None:
    alice, bob, _, _ = await users(env, admin)
    insert(admin, "spotify_oauth_flows", owner_id=alice.user_id, state_hash=digest("s"),
           session_hash=digest("x"), client_id=CLIENT_A, redirect_uri="u", verifier="A" * 43,
           expires_at="2999-01-01", created_at="2998-12-31 23:55")  # fmt: skip
    bob_before = state(admin, bob.user_id)
    admin.execute(text("DELETE FROM users WHERE id = :o"), {"o": alice.user_id})
    for table in ("spotify_pool", "class_playlists", "spotify_oauth_flows", "external_operations",
                  "connections"):  # fmt: skip
        left = admin.execute(
            text(f"SELECT count(*) FROM {table} WHERE owner_id = :o"),  # noqa: S608
            {"o": alice.user_id},
        ).scalar_one()
        assert left == 0, table
    assert state(admin, bob.user_id) == bob_before


async def test_the_purge_removes_expired_and_used_flows_of_every_user(
    env: Env, admin: Connection
) -> None:
    owners = [await env.user("a@example.com"), await env.user("b@example.com")]
    for owner in owners:
        for name, created, expires, used in (
            ("live", "now()", "now() + interval '10 minutes'", None),
            ("expired", "now() - interval '11 minutes'", "now() - interval '1 minute'", None),
            ("used", "now()", "now() + interval '10 minutes'", "now()"),
        ):
            # Test constants only: the times are SQL expressions.
            statement = (
                "INSERT INTO spotify_oauth_flows (owner_id, state_hash, session_hash, client_id, "  # noqa: S608
                "redirect_uri, verifier, created_at, expires_at, used_at) VALUES "
                f"(:o, :h, :h, :c, 'u', :v, {created}, {expires}, {used or 'NULL'})"
            )
            admin.execute(
                text(statement), {"o": owner, "h": digest(name), "c": CLIENT_A, "v": "A" * 43}
            )
    counts = await auth.purge_expired(env.factory, env.settings)
    assert counts["spotify_oauth_flows"] == 4
    left = admin.execute(text("SELECT owner_id, state_hash FROM spotify_oauth_flows")).all()
    assert sorted((r.owner_id, bytes(r.state_hash)) for r in left) == sorted(
        (owner, digest("live")) for owner in owners
    )
