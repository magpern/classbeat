"""``connections.generation`` (ADR 0009 section 5.4) and the Spotify flip rules (section 6; issue
SP1, #174): ``hold``, ``record_result`` and the flips compare the generation; a connect or
reconnect increases it, a rewrap or a refresh does not; a Spotify connection flips only for
``invalid_grant`` or ``app_misconfigured`` and only on the row's current token (id, generation
and ``wrapped_dek``). MOWL's behaviour is unchanged (``test_connections.py``,
``test_design_guard.py`` and the attach and save tests run as before)."""

import uuid
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import text

from authtools import Env
from classbeat import connections
from classbeat.connections import ConnectionFailure, OpenConnection
from classbeat.crypto.envelope import canonical_json, connection_aad, connection_wrap_aad, seal
from classbeat.db import user_session
from ctx import make_context
from keystools import KEY_2, ring_for, rotate, settings_for
from test_connection_errors import FakeMowlError, http_error

CLIENT_ID = "0123456789abcdef0123456789abcdef"
SPOTIFY_PUBLIC = {"client_id": CLIENT_ID, "spotify_user_id": "user-a"}


def tokens(access: str = "access-1", refresh: str = "refresh-1") -> dict[str, Any]:
    return {"access_token": access, "refresh_token": refresh, "expires_at": 2_000_000_000}


async def save_spotify(env: Env, ctx: Any, access: str = "access-1") -> uuid.UUID:
    return await connections.save(
        env.factory, env.app.state.keyring, ctx, "spotify", tokens(access), public=SPOTIFY_PUBLIC
    )


async def open_spotify(env: Env, ctx: Any) -> OpenConnection:
    return await connections.open_connection(env.factory, env.app.state.keyring, ctx, "spotify")


async def row(env: Env, ctx: Any, provider: str = "spotify") -> Any:
    async with user_session(env.factory, ctx) as db:
        return (
            await db.execute(
                text(
                    "SELECT status, last_error, generation, secret, wrapped_dek, key_id, "
                    "updated_at FROM connections WHERE provider = :p"
                ),
                {"p": provider},
            )
        ).one()


async def hold(env: Env, ctx: Any, opened: OpenConnection) -> bool:
    async with user_session(env.factory, ctx) as db:
        return await connections.hold(db, ctx, opened)


async def refresh(env: Env, ctx: Any, access: str) -> None:
    """What a refresh writes (ADR 0009 section 6): new tokens under a new data key, the same
    ``id`` and ``generation``. The refresh itself is issue SC1's; this is its row effect."""
    async with user_session(env.factory, ctx) as db:
        found = (
            await db.execute(
                text("SELECT id FROM connections WHERE provider = 'spotify' FOR UPDATE")
            )
        ).one()
        kek = await env.app.state.keyring.current(db)
        sealed = seal(
            canonical_json(tokens(access, "refresh-2")),
            connection_aad(ctx.user_id, "spotify", found.id),
            kek,
            connection_wrap_aad(kek.key_id, ctx.user_id, "spotify", found.id),
        )
        await db.execute(
            text(
                "UPDATE connections SET secret = :s, secret_nonce = :n, wrapped_dek = :w, "
                "dek_nonce = :d, key_id = :k WHERE id = :i"
            ),
            {
                "s": sealed.ciphertext, "n": sealed.nonce, "w": sealed.wrapped_dek,
                "d": sealed.dek_nonce, "k": sealed.key_id, "i": found.id,
            },
        )  # fmt: skip


async def test_a_connect_starts_at_one_and_each_reconnect_increases_it(env: Env) -> None:
    ctx = make_context(await env.user())
    ring = env.app.state.keyring
    await connections.save(env.factory, ring, ctx, "mowl", {"token": "t-1"})
    first = await connections.open_connection(env.factory, ring, ctx, "mowl")
    assert first.generation == 1
    await connections.save(env.factory, ring, ctx, "mowl", {"token": "t-2"})
    second = await connections.open_connection(env.factory, ring, ctx, "mowl")
    assert (second.id, second.generation) == (first.id, 2)
    # The stale generation: no hold, no flip, no result written.
    assert await hold(env, ctx, first) is False
    assert await connections.mark_needs_reconnect(env.factory, ctx, first) is False
    assert await connections.record_result(env.factory, ctx, first, "unknown") is False
    assert await hold(env, ctx, second) is True
    assert (await row(env, ctx, "mowl")).status == "connected"
    # A disconnect and a new connect is a new row (a new id), which starts at 1 again.
    await connections.disconnect(env.factory, ctx, "mowl")
    await connections.save(env.factory, ring, ctx, "mowl", {"token": "t-3"})
    third = await connections.open_connection(env.factory, ring, ctx, "mowl")
    assert (third.generation, third.id != first.id) == (1, True)
    assert await hold(env, ctx, first) is False


async def test_a_rewrap_keeps_the_generation_and_a_running_jobs_hold(
    env: Env, app_database_url: str, kek_dir: Path
) -> None:
    ctx = make_context(await env.user())
    await connections.save(env.factory, env.app.state.keyring, ctx, "mowl", {"token": "t-1"})
    await save_spotify(env, ctx)
    mowl = await connections.open_connection(env.factory, env.app.state.keyring, ctx, "mowl")
    spotify = await open_spotify(env, ctx)
    rotate(kek_dir, "kek-2", KEY_2, old_id="kek-1")
    ring, engine = await ring_for(settings_for(app_database_url, "kek-2"))
    try:
        counts = await connections.rewrap_owner(env.factory, ring, ctx)
        assert counts == {"kek-1 rewrapped": 2}
        after = await row(env, ctx)
        assert (after.key_id, after.generation) == ("kek-2", 1)
        assert bytes(after.wrapped_dek) != spotify.version
        # The jobs that opened before the rewrap keep their hold (an improvement on wrapped_dek).
        assert await hold(env, ctx, mowl) is True
        assert await hold(env, ctx, spotify) is True
        assert await connections.record_result(env.factory, ctx, mowl, None) is True
        # A MOWL 401 from before the rewrap still flips (same generation; MOWL unchanged) ...
        assert await connections.mark_needs_reconnect(env.factory, ctx, mowl) is True
        # ... but a Spotify flip with the pre-rewrap token writes zero rows (wrapped_dek changed).
        assert (
            await connections.mark_needs_reconnect(env.factory, ctx, spotify, "invalid_grant")
            is False
        )
        assert (await row(env, ctx)).status == "connected"
    finally:
        await engine.dispose()


async def test_a_refresh_keeps_the_hold_and_a_stale_spotify_flip_writes_nothing(env: Env) -> None:
    ctx = make_context(await env.user())
    await save_spotify(env, ctx)
    stale = await open_spotify(env, ctx)
    await refresh(env, ctx, "access-2")
    fresh = await open_spotify(env, ctx)
    assert fresh.credentials.spotify_access_token == "access-2"
    assert (fresh.id, fresh.generation) == (stale.id, stale.generation)
    assert fresh.version != stale.version
    assert await hold(env, ctx, stale) is True  # a refresh does not invalidate a running job
    before = await row(env, ctx)
    # A 401 answered for the pre-refresh token: zero rows.
    for category in ("invalid_grant", "app_misconfigured"):
        assert await connections.mark_needs_reconnect(env.factory, ctx, stale, category) is False
    assert await row(env, ctx) == before
    # On the current token it flips: status, last_error and updated_at only.
    assert await connections.mark_needs_reconnect(env.factory, ctx, fresh, "invalid_grant")
    after = await row(env, ctx)
    assert (after.status, after.last_error) == ("needs_reconnect", "invalid_grant")
    assert (after.generation, bytes(after.secret), bytes(after.wrapped_dek), after.key_id) == (
        before.generation, bytes(before.secret), bytes(before.wrapped_dek), before.key_id,
    )  # fmt: skip
    assert after.updated_at >= before.updated_at
    assert await hold(env, ctx, fresh) is False  # no longer connected


async def test_a_stale_spotify_flip_after_a_reconnect_does_nothing(env: Env) -> None:
    ctx = make_context(await env.user())
    await save_spotify(env, ctx)
    stale = await open_spotify(env, ctx)
    await save_spotify(env, ctx, "access-new")  # the user's reconnect: generation 2
    assert (await row(env, ctx)).generation == 2
    assert await hold(env, ctx, stale) is False
    assert (
        await connections.mark_needs_reconnect(env.factory, ctx, stale, "app_misconfigured")
        is False
    )
    assert await connections.record_result(env.factory, ctx, stale, "forbidden") is False
    current = await row(env, ctx)
    assert (current.status, current.last_error) == ("connected", None)


@pytest.mark.parametrize(
    "category",
    ["needs_reconnect", "forbidden", "not_found", "not_allowlisted", "rate_limited", "unknown"],
)
async def test_only_the_token_endpoint_categories_flip_spotify(env: Env, category: str) -> None:
    ctx = make_context(await env.user())
    await save_spotify(env, ctx)
    opened = await open_spotify(env, ctx)
    with pytest.raises(ValueError, match="invalid_grant or app_misconfigured"):
        await connections.mark_needs_reconnect(env.factory, ctx, opened, category)
    assert (await row(env, ctx)).status == "connected"


@pytest.mark.parametrize("status", [401, 403])
async def test_a_spotify_call_failing_401_or_403_does_not_flip(env: Env, status: int) -> None:
    ctx = make_context(await env.user())
    await save_spotify(env, ctx)
    opened = await open_spotify(env, ctx)

    def call(_: Any) -> None:
        error = FakeMowlError("refused")
        error.__cause__ = http_error(status)
        raise error

    with pytest.raises(ConnectionFailure) as raised:
        await connections.call_opened(env.factory, ctx, opened, call, library_error=FakeMowlError)
    assert raised.value.category == "needs_reconnect"
    current = await row(env, ctx)
    assert (current.status, current.generation) == ("connected", 1)


async def test_a_mowl_call_failing_401_still_flips(env: Env) -> None:
    ctx = make_context(await env.user())
    await connections.save(env.factory, env.app.state.keyring, ctx, "mowl", {"token": "t-1"})
    opened = await connections.open_connection(env.factory, env.app.state.keyring, ctx, "mowl")

    def call(_: Any) -> None:
        error = FakeMowlError("refused")
        error.__cause__ = http_error(401)
        raise error

    with pytest.raises(ConnectionFailure):
        await connections.call_opened(env.factory, ctx, opened, call, library_error=FakeMowlError)
    current = await row(env, ctx, "mowl")
    assert (current.status, current.last_error) == ("needs_reconnect", "needs_reconnect")


async def test_another_users_generation_is_never_touched(env: Env) -> None:
    alice, bob = (
        make_context(await env.user("a@example.com")),
        make_context(await env.user("b@example.com")),
    )
    await save_spotify(env, alice)
    await save_spotify(env, bob)
    opened_bob = await open_spotify(env, bob)
    await save_spotify(env, alice, "access-new")
    assert (await row(env, alice)).generation == 2
    assert (await row(env, bob)).generation == 1
    assert await hold(env, bob, opened_bob) is True
    # Bob's opened connection under Alice's context matches nothing (owner and RLS).
    assert await hold(env, alice, opened_bob) is False
    assert (
        await connections.mark_needs_reconnect(env.factory, alice, opened_bob, "invalid_grant")
        is False
    )
    assert (await row(env, bob)).status == "connected"
