"""Rotation and ``keys rewrap`` (ADR 0004 section 2): connections and TOTP, also concurrently."""

import asyncio
from pathlib import Path

import pytest
from sqlalchemy import text

from authtools import TEST_KEK, Env
from classbeat import connections
from classbeat.auth.totp_keys import TotpUnreadable, open_totp_secret, rewrap_v2_secrets
from classbeat.db import system_session, user_session
from ctx import make_context
from keystools import (
    KEY_2,
    TOKENS,
    TOTP_SECRET,
    add_totp,
    cli,
    connect,
    ring_for,
    rotate,
    settings_for,
    token_of,
)


async def totp_of(env: Env, ring, user_id) -> bytes:
    async with system_session(env.factory) as db:
        row = (
            await db.execute(
                text(
                    "SELECT secret_ciphertext, nonce, key_id, wrapped_dek, dek_nonce "
                    "FROM mfa_totp WHERE user_id = :u"
                ),
                {"u": user_id},
            )
        ).one()
        return await open_totp_secret(db, env.app.state.keys, ring, user_id, row)


async def key_ids(env: Env, user_id) -> set[str]:
    async with user_session(env.factory, make_context(user_id)) as db:
        return set((await db.execute(text("SELECT key_id FROM connections"))).scalars())


async def test_rotate_then_rewrap_moves_every_row_and_keeps_the_payload(
    env: Env, app_database_url: str, kek_dir: Path
) -> None:
    old_ring = env.app.state.keyring
    users = {email: await env.user(email) for email in TOKENS}
    for email, user_id in users.items():
        await connect(env.factory, old_ring, user_id, TOKENS[email])
    await add_totp(env, old_ring, users["a@example.com"])
    secret_before = await env.scalar("SELECT secret_ciphertext FROM mfa_totp")

    rotate(kek_dir, "kek-2", KEY_2, old_id="kek-1")
    settings = settings_for(app_database_url, "kek-2")
    ring, engine = await ring_for(settings)
    try:
        # Before the rewrap the retired KEK still opens the old rows.
        for email, user_id in users.items():
            assert await token_of(env.factory, ring, user_id) == TOKENS[email]
        code, out, err = await cli(settings, "keys", "rewrap")
        assert (code, err) == (0, "")
        assert "kek-1 rewrapped: 2" in out
        assert "kek-1 rewrapped: 1" in out  # the TOTP row
        assert "connections per key_id now:\n  kek-2: 2" in out
        assert "TOTP per key_id now:\n  kek-2: 1" in out
        for secret in (*TOKENS.values(), TEST_KEK.hex(), KEY_2.hex(), *map(str, users.values())):
            assert secret not in out
        for user_id in users.values():
            assert await key_ids(env, user_id) == {"kek-2"}
        # Only the wrap changed: the payload ciphertext of the TOTP row is the same bytes.
        assert await env.scalar("SELECT secret_ciphertext FROM mfa_totp") == secret_before
        # The old KEK can go once rewrap reports zero rows for it.
        (kek_dir / "connection_kek_kek-1").unlink()
        fresh, fresh_engine = await ring_for(settings)
        try:
            for email, user_id in users.items():
                assert await token_of(env.factory, fresh, user_id) == TOKENS[email]
            assert await totp_of(env, fresh, users["a@example.com"]) == TOTP_SECRET
        finally:
            await fresh_engine.dispose()
        code, out, _ = await cli(settings, "keys", "rewrap")
        assert code == 0
        assert "connections rewrapped (old key_id outcome):\n  (none)" in out
    finally:
        await engine.dispose()


async def test_rewrap_without_the_retired_file_reports_and_changes_nothing(
    env: Env, app_database_url: str, kek_dir: Path
) -> None:
    user_id = await env.user("a@example.com")
    await connect(env.factory, env.app.state.keyring, user_id, TOKENS["a@example.com"])
    rotate(kek_dir, "kek-2", KEY_2, old_id="kek-1")
    (kek_dir / "connection_kek_kek-1").unlink()
    settings = settings_for(app_database_url, "kek-2")
    code, out, err = await cli(settings, "keys", "rewrap")
    assert code == 1
    assert "kek-1 key_unavailable: 1" in out
    assert "could not be rewrapped" in err
    assert await key_ids(env, user_id) == {"kek-1"}
    ring, engine = await ring_for(settings)
    try:
        with pytest.raises(connections.ConnectionFailure, match="key_unavailable"):
            await token_of(env.factory, ring, user_id)
        info = (await connections.list_connections(env.factory, make_context(user_id)))[0]
        assert info.status == "connected"  # an operator error never flips the row
    finally:
        await engine.dispose()


async def test_concurrent_rewraps_and_a_reconnect_lose_nothing(
    env: Env, app_database_url: str, kek_dir: Path
) -> None:
    old_ring = env.app.state.keyring
    user_ids = [await env.user(f"u{i}@example.com") for i in range(6)]
    for i, user_id in enumerate(user_ids):
        await connect(env.factory, old_ring, user_id, f"0000000{i}-1111-2222-3333-444444444444")
    rotate(kek_dir, "kek-2", KEY_2, old_id="kek-1")
    settings = settings_for(app_database_url, "kek-2")
    ring, engine = await ring_for(settings)
    reconnected = "99999999-1111-2222-3333-444444444444"
    try:
        contexts = [make_context(u) for u in user_ids]

        async def one_pass() -> int:
            total = 0
            for ctx in contexts:
                counts = await connections.rewrap_owner(env.factory, ring, ctx)
                total += counts["kek-1 rewrapped"]
            return total

        results = await asyncio.gather(
            one_pass(),
            one_pass(),
            connections.save(env.factory, ring, contexts[0], "mowl", {"token": reconnected}),
        )
        # Each row moved exactly once (or was rewritten by the reconnect under kek-2).
        assert results[0] + results[1] in (5, 6)
        for user_id in user_ids:
            assert await key_ids(env, user_id) == {"kek-2"}
        (kek_dir / "connection_kek_kek-1").unlink()
        fresh, fresh_engine = await ring_for(settings)
        try:
            assert await token_of(env.factory, fresh, user_ids[0]) == reconnected
            for i, user_id in enumerate(user_ids[1:], start=1):
                expected = f"0000000{i}-1111-2222-3333-444444444444"
                assert await token_of(env.factory, fresh, user_id) == expected
        finally:
            await fresh_engine.dispose()
    finally:
        await engine.dispose()


async def test_totp_v1_still_decrypts_and_rewrap_totp_migrates_it(
    env: Env, app_database_url: str
) -> None:
    ring = env.app.state.keyring
    v1_user, v2_user = await env.user("v1@example.com"), await env.user("v2@example.com")
    await add_totp(env, ring, v1_user, v1=True)
    await add_totp(env, ring, v2_user)
    assert await totp_of(env, ring, v1_user) == TOTP_SECRET  # app-v1 keeps working
    v2_before = await env.row("SELECT * FROM mfa_totp WHERE user_id = :u", u=v2_user)

    code, out, _ = await cli(settings_for(app_database_url, "kek-1"), "keys", "rewrap", "--totp")
    assert code == 0
    assert "TOTP app-v1 -> v2:\n  app-v1 migrated: 1" in out
    row = await env.row("SELECT * FROM mfa_totp WHERE user_id = :u", u=v1_user)
    assert row.key_id == "kek-1"
    assert len(bytes(row.wrapped_dek)) == 48
    assert await totp_of(env, ring, v1_user) == TOTP_SECRET
    v2_after = await env.row("SELECT * FROM mfa_totp WHERE user_id = :u", u=v2_user)
    assert v2_after == v2_before  # a v2 row is never re-encrypted by --totp
    code, out, _ = await cli(settings_for(app_database_url, "kek-1"), "keys", "rewrap", "--totp")
    assert "TOTP app-v1 -> v2:\n  (none)" in out


async def test_a_tampered_v2_totp_row_is_unreadable_not_rewrapped(
    env: Env, app_database_url: str, kek_dir: Path
) -> None:
    user_id = await env.user()
    await add_totp(env, env.app.state.keyring, user_id)
    await env.run("UPDATE mfa_totp SET wrapped_dek = decode(repeat('00', 48), 'hex')")
    with pytest.raises(TotpUnreadable) as raised:
        await totp_of(env, env.app.state.keyring, user_id)
    assert raised.value.kind == "totp_decrypt_failed"
    rotate(kek_dir, "kek-2", KEY_2, old_id="kek-1")
    ring, engine = await ring_for(settings_for(app_database_url, "kek-2"))
    try:
        assert await rewrap_v2_secrets(env.factory, ring) == {"kek-1 decrypt_failed": 1}
    finally:
        await engine.dispose()


async def test_rewrap_exits_non_zero_when_a_totp_row_cannot_be_rewrapped(
    env: Env, app_database_url: str, kek_dir: Path
) -> None:
    user_id = await env.user()
    await add_totp(env, env.app.state.keyring, user_id)
    await env.run("UPDATE mfa_totp SET wrapped_dek = decode(repeat('00', 48), 'hex')")
    rotate(kek_dir, "kek-2", KEY_2, old_id="kek-1")
    code, out, err = await cli(settings_for(app_database_url, "kek-2"), "keys", "rewrap")
    assert code == 1
    assert "kek-1 decrypt_failed: 1" in out
    assert "1 TOTP rows could not be migrated or rewrapped" in err
    assert "connection rows" not in err


async def test_rewrap_totp_exits_non_zero_when_a_v1_row_cannot_be_migrated(
    env: Env, app_database_url: str
) -> None:
    user_id = await env.user()
    await add_totp(env, env.app.state.keyring, user_id, v1=True)
    await env.run("UPDATE mfa_totp SET secret_ciphertext = secret_ciphertext || '\\x00'::bytea")
    code, out, err = await cli(settings_for(app_database_url, "kek-1"), "keys", "rewrap", "--totp")
    assert code == 1
    assert "app-v1 decrypt_failed: 1" in out
    assert "1 TOTP rows could not be migrated or rewrapped" in err
    assert await env.scalar("SELECT key_id FROM mfa_totp") == "app-v1"  # untouched


async def test_a_decrypt_failed_flip_bumps_updated_at(
    env: Env, app_database_url: str, kek_dir: Path
) -> None:
    user_id = await env.user("a@example.com")
    await connect(env.factory, env.app.state.keyring, user_id, TOKENS["a@example.com"])
    ctx = make_context(user_id)
    async with user_session(env.factory, ctx) as db:
        await db.execute(
            text("UPDATE connections SET wrapped_dek = decode(repeat('00', 48), 'hex')")
        )
        before = (await db.execute(text("SELECT updated_at FROM connections"))).scalar_one()
    rotate(kek_dir, "kek-2", KEY_2, old_id="kek-1")
    code, out, _ = await cli(settings_for(app_database_url, "kek-2"), "keys", "rewrap")
    assert code == 1
    assert "kek-1 decrypt_failed: 1" in out
    async with user_session(env.factory, ctx) as db:
        row = (
            await db.execute(text("SELECT status, last_error, updated_at FROM connections"))
        ).one()
    assert (row.status, row.last_error) == ("needs_reconnect", "decrypt_failed")
    assert row.updated_at > before
