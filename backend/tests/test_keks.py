"""KEK files, the ``connection_keks`` check and the startup refusals (ADR 0004 section 2)."""

from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError

from authtools import TEST_KEK, Env, make_settings
from classbeat.config import Settings
from classbeat.crypto import keks
from classbeat.crypto.envelope import check_value
from classbeat.web import create_app

OTHER_KEY = bytes(range(100, 132))


def write_key(directory: Path, name: str, content: bytes) -> None:
    (directory / name).write_bytes(content)


@pytest.mark.parametrize("content", [TEST_KEK.hex().encode(), TEST_KEK.hex().encode() + b"\n"])
def test_a_valid_file_is_read(tmp_path: Path, content: bytes) -> None:
    write_key(tmp_path, "connection_kek", content)
    kek = keks.read_current("kek-1", tmp_path)
    assert (kek.key_id, kek.key) == ("kek-1", TEST_KEK)


@pytest.mark.parametrize(
    "content",
    [
        TEST_KEK.hex().upper().encode(),
        TEST_KEK.hex().encode() + b"\r\n",
        TEST_KEK.hex().encode() + b"\n\n",
        TEST_KEK.hex().encode()[:-1],
        TEST_KEK.hex().encode() + b"0",
        b" " + TEST_KEK.hex().encode(),
        b"",
        TEST_KEK,  # raw bytes, not hex
    ],
)
def test_a_malformed_file_refuses_without_showing_it(tmp_path: Path, content: bytes) -> None:
    write_key(tmp_path, "connection_kek", content)
    with pytest.raises(keks.KekError, match="64 lowercase hexadecimal") as raised:
        keks.read_current("kek-1", tmp_path)
    assert TEST_KEK.hex() not in str(raised.value).lower()


def test_a_missing_file_or_a_bad_id_refuses(tmp_path: Path) -> None:
    with pytest.raises(keks.KekError, match="missing"):
        keks.read_current("kek-1", tmp_path)
    write_key(tmp_path, "connection_kek", TEST_KEK.hex().encode())
    for bad in ("app-v1", "kek-", "kek-UPPER", "kek-1/../x"):
        with pytest.raises(keks.KekError, match="does not match"):
            keks.read_current(bad, tmp_path)


def test_settings_refuse_a_bad_kek_id(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CLASSBEAT_CONNECTION_KEK_ID", "kek-OK")
    with pytest.raises(ValueError, match="connection_kek_id"):
        Settings()
    monkeypatch.setenv("CLASSBEAT_CONNECTION_KEK_ID", "kek-2026-a")
    assert Settings().connection_kek_id == "kek-2026-a"


async def test_startup_records_the_check_value_once(env: Env) -> None:
    rows = await env.row("SELECT count(*) AS n, min(key_id) AS k FROM connection_keks")
    assert (rows.n, rows.k) == (1, "kek-1")
    stored = await env.scalar("SELECT check_value FROM connection_keks")
    assert bytes(stored) == check_value(TEST_KEK)  # pyright: ignore[reportArgumentType]
    assert TEST_KEK not in bytes(stored)  # pyright: ignore[reportArgumentType]
    ring = await keks.load_keyring(env.factory, "kek-1")  # a second process: same row
    assert ring.verified
    assert await env.scalar("SELECT count(*) FROM connection_keks") == 1


async def test_web_refuses_to_start_on_a_swapped_or_missing_key(
    env: Env, app_database_url: str, kek_dir: Path
) -> None:
    write_key(kek_dir, "connection_kek", OTHER_KEY.hex().encode())
    app = create_app(make_settings(app_database_url))
    with pytest.raises(keks.KekError, match="does not match the check value"):
        async with app.router.lifespan_context(app):
            pytest.fail("must not start")
    (kek_dir / "connection_kek").unlink()
    app = create_app(make_settings(app_database_url))
    with pytest.raises(keks.KekError, match="missing"):
        async with app.router.lifespan_context(app):
            pytest.fail("must not start")


async def test_the_worker_refuses_to_start_on_a_swapped_key(
    env: Env, app_database_url: str, kek_dir: Path
) -> None:
    from classbeat.worker.__main__ import _run  # pyright: ignore[reportPrivateUsage]

    write_key(kek_dir, "connection_kek", OTHER_KEY.hex().encode())
    with pytest.raises(keks.KekError):
        await _run(make_settings(app_database_url))


async def test_an_unreachable_database_defers_the_check_to_first_use(
    unreachable_database_url: str, env: Env, kek_dir: Path
) -> None:
    unreachable = create_app(make_settings(unreachable_database_url))
    async with unreachable.router.lifespan_context(unreachable):
        assert unreachable.state.keyring.verified is False
    # On a reachable database with a swapped key the first use refuses.
    write_key(kek_dir, "connection_kek", OTHER_KEY.hex().encode())
    ring = keks.KeyRing(keks.read_current("kek-1"), kek_dir, verified=False, factory=env.factory)
    async with env.db() as db:
        with pytest.raises(keks.KekError):
            await ring.current(db)
    assert ring.verified is False


async def test_a_rolled_back_caller_does_not_undo_the_deferred_check(
    env: Env, kek_dir: Path, database_url: str
) -> None:
    """The first-use check runs in its own transaction: the check row is committed and the ring
    verified even when the caller's transaction rolls back afterwards."""
    engine = create_engine(database_url)
    with engine.begin() as admin:  # the superuser: the app role may not delete check rows
        admin.execute(text("DELETE FROM connection_keks"))
    engine.dispose()
    ring = keks.KeyRing(keks.read_current("kek-1"), kek_dir, verified=False, factory=env.factory)

    class Rollback(Exception):
        pass

    with pytest.raises(Rollback):
        async with env.db() as db:
            await ring.current(db)
            raise Rollback
    assert ring.verified
    assert await env.scalar("SELECT count(*) FROM connection_keks") == 1


def test_an_unverified_ring_needs_a_factory(kek_dir: Path) -> None:
    with pytest.raises(ValueError, match="session factory"):
        keks.KeyRing(keks.read_current("kek-1"), kek_dir, verified=False)


async def test_retired_keys_load_only_with_a_matching_check_row(env: Env, kek_dir: Path) -> None:
    # kek-0 was current once (it has a row); kek-1 is current now.
    await env.run(
        "INSERT INTO connection_keks (key_id, check_value) VALUES ('kek-0', :v)",
        v=check_value(OTHER_KEY),
    )
    ring = env.app.state.keyring
    async with env.db() as db:
        with pytest.raises(keks.KeyUnavailable):  # no file yet
            await ring.kek_for(db, "kek-0")
    write_key(kek_dir, "connection_kek_kek-0", b"ab" * 32)  # a different key under that id
    async with env.db() as db:
        with pytest.raises(keks.KeyUnavailable):
            await ring.kek_for(db, "kek-0")
    write_key(kek_dir, "connection_kek_kek-0", OTHER_KEY.hex().encode() + b"\n")
    async with env.db() as db:
        assert (await ring.kek_for(db, "kek-0")).key == OTHER_KEY
    write_key(kek_dir, "connection_kek_kek-7", b"cd" * 32)  # a file with no row
    async with env.db() as db:
        with pytest.raises(keks.KeyUnavailable):
            await ring.kek_for(db, "kek-7")
        for bad in ("../connection_kek", "app-v1", "kek-1/x", ""):
            with pytest.raises(keks.KeyUnavailable):
                await ring.kek_for(db, bad)
    assert "ab" * 4 not in repr(ring)


async def test_the_app_role_cannot_alter_the_check_table(env: Env) -> None:
    from sqlalchemy.exc import ProgrammingError

    async with env.db() as db:
        with pytest.raises(ProgrammingError, match=r"must be owner|permission denied"):
            await db.execute(text("TRUNCATE connection_keks"))


async def test_the_app_role_may_record_and_read_check_rows_but_not_change_or_forget_them(
    env: Env,
) -> None:
    """Migration 0003 grants classbeat_app SELECT and INSERT on connection_keks only."""
    await env.run(
        "INSERT INTO connection_keks (key_id, check_value) VALUES ('kek-9', :v)",
        v=check_value(OTHER_KEY),
    )
    assert await env.scalar("SELECT count(*) FROM connection_keks WHERE key_id = 'kek-9'") == 1
    for statement in (
        "UPDATE connection_keks SET check_value = :v WHERE key_id = 'kek-9'",
        "DELETE FROM connection_keks WHERE key_id = 'kek-9'",
        "DELETE FROM connection_keks",
    ):
        with pytest.raises(DBAPIError, match="permission denied"):
            await env.run(statement, v=check_value(TEST_KEK))
    assert await env.scalar("SELECT count(*) FROM connection_keks WHERE key_id = 'kek-9'") == 1
