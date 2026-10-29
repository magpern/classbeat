"""The connections repository against Postgres (ADR 0004 sections 1 to 5, 7), driven directly
with fake credentials and a fake library error. The MOWL connect route and the real library are
tested in ``test_mowl_connect.py``."""

import asyncio
import logging
import pickle
import uuid
from typing import Any
from urllib.error import HTTPError

import pytest
from sqlalchemy import create_engine, text

from authtools import Env
from classbeat import connections
from classbeat.auth.deps import issue_context
from classbeat.connections import ConnectionFailure, Credentials
from classbeat.db import create_session_factory, system_session, user_session
from classbeat.egress import OutboundBlocked
from classbeat.log import JsonFormatter
from ctx import make_context
from keystools import TOTP_SECRET, add_totp
from test_connection_errors import TICKET, FakeMowlError, http_error

TOKEN = "11111111-2222-3333-4444-555555555555"
NEW_TOKEN = "66666666-7777-8888-9999-000000000000"
PASSWORD = "hunter2-correct-horse"


def logged(caplog: pytest.LogCaptureFixture) -> str:
    """Everything logged, the way production writes it (``extra`` fields included, which
    ``caplog.text`` leaves out), plus each record's raw attributes: the formatter masks values
    under secret-looking keys, and a leak under another key must still show."""
    formatter = JsonFormatter()
    return "\n".join(
        [caplog.text]
        + [formatter.format(record) for record in caplog.records]
        + [repr(record.__dict__) for record in caplog.records]
    )


async def connected(env: Env, email: str = "a@example.com", token: str = TOKEN) -> Any:
    ctx = make_context(await env.user(email))
    await connections.save(env.factory, env.app.state.keyring, ctx, "mowl", {"token": token})
    return ctx


async def test_round_trip_returns_credentials_only(env: Env) -> None:
    ctx = await connected(env)
    opened = await connections.open_connection(env.factory, env.app.state.keyring, ctx, "mowl")
    assert isinstance(opened.credentials, Credentials)
    assert opened.credentials.mowl_token == TOKEN
    assert opened.provider == "mowl"
    for shown in (repr(opened), str(opened), repr(opened.credentials), str(opened.credentials)):
        assert TOKEN not in shown
    for value in (opened, opened.credentials):
        with pytest.raises(TypeError):
            pickle.dumps(value)
    info = await connections.list_connections(env.factory, ctx)
    assert [(i.provider, i.status, i.last_error) for i in info] == [("mowl", "connected", None)]
    assert TOKEN not in repr(info)


async def test_the_stored_row_is_ciphertext_with_the_adr_shape(env: Env) -> None:
    ctx = await connected(env)
    async with user_session(env.factory, ctx) as db:
        row = (await db.execute(text("SELECT * FROM connections"))).one()
    assert TOKEN.encode() not in bytes(row.secret)
    assert (len(row.secret_nonce), len(row.wrapped_dek), len(row.dek_nonce)) == (12, 48, 12)
    assert (row.key_id, row.status, row.public, row.expires_at) == ("kek-1", "connected", {}, None)


async def test_reconnect_keeps_the_id_and_writes_a_new_data_key(env: Env) -> None:
    ctx = await connected(env)
    ring = env.app.state.keyring
    first = await connections.open_connection(env.factory, ring, ctx, "mowl")
    await connections.mark_needs_reconnect(env.factory, ctx, first)
    again = await connections.save(env.factory, ring, ctx, "mowl", {"token": NEW_TOKEN})
    second = await connections.open_connection(env.factory, ring, ctx, "mowl")
    assert again == first.id == second.id
    assert second.version != first.version
    assert second.credentials.mowl_token == NEW_TOKEN
    info = (await connections.list_connections(env.factory, ctx))[0]
    assert (info.status, info.last_error) == ("connected", None)


async def test_a_stale_status_change_does_not_touch_the_fresh_connection(env: Env) -> None:
    ctx = await connected(env)
    ring = env.app.state.keyring
    stale = await connections.open_connection(env.factory, ring, ctx, "mowl")
    await connections.save(env.factory, ring, ctx, "mowl", {"token": NEW_TOKEN})  # reconnect
    assert await connections.mark_needs_reconnect(env.factory, ctx, stale) is False
    assert await connections.record_result(env.factory, ctx, stale, "unknown") is False
    info = (await connections.list_connections(env.factory, ctx))[0]
    assert (info.status, info.last_error) == ("connected", None)


async def test_a_parallel_connect_is_retried_once_and_keeps_the_winners_id(env: Env) -> None:
    ctx = await connected(env, token=TOKEN)
    ring = env.app.state.keyring
    original = (await connections.list_connections(env.factory, ctx))[0].id
    await connections.disconnect(env.factory, ctx, "mowl")
    # Another connect holds an uncommitted INSERT: ours finds no row to lock, inserts, blocks on
    # the unique index, gets the violation once the other commits, and retries as an UPDATE.
    other = create_session_factory(env.app.state.engine)
    async with user_session(other, ctx) as db:
        await db.execute(
            text(
                "INSERT INTO connections (id, owner_id, provider, status, secret, secret_nonce, "
                "wrapped_dek, dek_nonce, key_id) VALUES (:id, :o, 'mowl', 'connected', '\\x00', "
                ":n, :w, :n, 'kek-1')"
            ),
            {"id": original, "o": ctx.user_id, "n": bytes(12), "w": bytes(48)},
        )
        racing = asyncio.create_task(
            connections.save(env.factory, ring, ctx, "mowl", {"token": NEW_TOKEN})
        )
        await asyncio.sleep(0.5)
        assert not racing.done()  # blocked on the unique index
    assert await racing == original
    opened = await connections.open_connection(env.factory, ring, ctx, "mowl")
    assert (opened.id, opened.credentials.mowl_token) == (original, NEW_TOKEN)


async def test_decrypt_failed_flips_the_row_and_key_unavailable_leaves_it(
    env: Env, caplog: pytest.LogCaptureFixture
) -> None:
    ctx = await connected(env)
    ring = env.app.state.keyring
    async with user_session(env.factory, ctx) as db:
        await db.execute(text("UPDATE connections SET secret = secret || '\\x00'::bytea"))
    with pytest.raises(ConnectionFailure) as raised:
        await connections.open_connection(env.factory, ring, ctx, "mowl")
    assert raised.value.args == ("decrypt_failed",)
    assert raised.value.__context__ is None
    info = (await connections.list_connections(env.factory, ctx))[0]
    assert (info.status, info.last_error) == ("needs_reconnect", "decrypt_failed")
    # Now not connected at all until a reconnect.
    with pytest.raises(ConnectionFailure, match="not_connected"):
        await connections.open_connection(env.factory, ring, ctx, "mowl")

    other = await connected(env, "b@example.com")
    async with user_session(env.factory, other) as db:
        await db.execute(text("UPDATE connections SET key_id = 'kek-retired'"))
    with pytest.raises(ConnectionFailure) as missing:
        await connections.open_connection(env.factory, ring, other, "mowl")
    assert missing.value.args == ("key_unavailable",)
    info = (await connections.list_connections(env.factory, other))[0]
    assert (info.status, info.last_error) == ("connected", None)  # not changed
    assert "connection_id" in caplog.text or any(
        r.__dict__.get("category") == "key_unavailable" for r in caplog.records
    )


async def test_rls_and_cross_user_lookups(env: Env, database_url: str) -> None:
    alice = await connected(env, "a@example.com")
    bob = await connected(env, "b@example.com", NEW_TOKEN)
    alice_id = (await connections.list_connections(env.factory, alice))[0].id
    # Bob's repository calls never see Alice's row: not found, the same as an unknown id (404).
    assert await connections.find(env.factory, bob, alice_id) is None
    assert await connections.find(env.factory, alice, alice_id) is not None
    assert await connections.find(env.factory, bob, uuid.uuid4()) is None
    # RLS underneath: raw SQL as Bob sees, updates and deletes none of Alice's rows.
    async with user_session(env.factory, bob) as db:
        assert (await db.execute(text("SELECT count(*) FROM connections"))).scalar_one() == 1
        changed = await db.execute(
            text("UPDATE connections SET status = 'needs_reconnect' WHERE id = :i"), {"i": alice_id}
        )
        assert changed.rowcount == 0  # pyright: ignore[reportAttributeAccessIssue]
        deleted = await db.execute(text("DELETE FROM connections WHERE id = :i"), {"i": alice_id})
        assert deleted.rowcount == 0  # pyright: ignore[reportAttributeAccessIssue]
    # A session without a context sees no rows at all.
    async with system_session(env.factory) as db:
        assert (await db.execute(text("SELECT count(*) FROM connections"))).scalar_one() == 0
    opened = await connections.open_connection(env.factory, env.app.state.keyring, alice, "mowl")
    assert opened.credentials.mowl_token == TOKEN
    # Alice's ciphertext copied into Bob's row does not decrypt there (AAD owner and id).
    admin = create_engine(database_url)
    try:
        with admin.begin() as conn:
            conn.execute(
                text(
                    "UPDATE connections b SET secret = a.secret, secret_nonce = a.secret_nonce, "
                    "wrapped_dek = a.wrapped_dek, dek_nonce = a.dek_nonce FROM connections a "
                    "WHERE a.owner_id = :a AND b.owner_id = :b"
                ),
                {"a": alice.user_id, "b": bob.user_id},
            )
    finally:
        admin.dispose()
    with pytest.raises(ConnectionFailure, match="decrypt_failed"):
        await connections.open_connection(env.factory, env.app.state.keyring, bob, "mowl")


async def test_the_issued_context_carries_connection_references_only(env: Env) -> None:
    ctx = await connected(env)
    row_id = (await connections.list_connections(env.factory, ctx))[0].id
    issued = await issue_context(env.factory, ctx.user_id, "user")
    assert issued.connections == frozenset({("mowl", row_id)})
    assert issued.connection("mowl") == row_id
    assert TOKEN not in repr(issued)
    other = await issue_context(env.factory, (await env.user("b@example.com")), "user")
    assert other.connections == frozenset()


async def test_use_maps_a_401_to_needs_reconnect_and_records_success(
    env: Env, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    ctx = await connected(env)
    ring = env.app.state.keyring
    seen: list[str] = []

    def ok(credentials: Credentials) -> str:
        seen.append(credentials.mowl_token)
        return "saved"

    assert await connections.use(env.factory, ring, ctx, "mowl", ok, library_error=FakeMowlError)
    assert seen == [TOKEN]
    assert (await connections.list_connections(env.factory, ctx))[0].last_used_at is not None

    def rate_limited(credentials: Credentials) -> None:
        error = FakeMowlError(f"slow down {credentials.mowl_token} {TICKET}")
        raise error from http_error(429, "5")

    with pytest.raises(ConnectionFailure) as limited:
        await connections.use(
            env.factory, ring, ctx, "mowl", rate_limited, library_error=FakeMowlError
        )
    assert (limited.value.args, limited.value.retry_after) == (("rate_limited",), 5)
    info = (await connections.list_connections(env.factory, ctx))[0]
    assert (info.status, info.last_error) == ("connected", "rate_limited")

    def expired(credentials: Credentials) -> None:
        raise FakeMowlError(f"401 for {credentials.mowl_token}") from http_error(401)

    with pytest.raises(ConnectionFailure) as raised:
        await connections.use(env.factory, ring, ctx, "mowl", expired, library_error=FakeMowlError)
    assert raised.value.args == ("needs_reconnect",)
    assert raised.value.__cause__ is None
    assert raised.value.__context__ is None
    info = (await connections.list_connections(env.factory, ctx))[0]
    assert (info.status, info.last_error) == ("needs_reconnect", "needs_reconnect")
    with pytest.raises(ConnectionFailure, match="not_connected"):
        await connections.use(env.factory, ring, ctx, "mowl", ok, library_error=FakeMowlError)
    assert seen == [TOKEN]  # no provider call once it needs a reconnect
    for secret in (TOKEN, TICKET):
        assert secret not in logged(caplog)


async def test_an_allowlist_refusal_in_use_leaves_the_connection_untouched(env: Env) -> None:
    """ADR 0006 section 9: the refusal is not the connection's fault, so no status or last_error."""
    ctx = await connected(env)

    def blocked(credentials: Credentials) -> None:
        raise OutboundBlocked("evil.example")

    before = (await connections.list_connections(env.factory, ctx))[0]
    with pytest.raises(OutboundBlocked):
        await connections.use(
            env.factory, env.app.state.keyring, ctx, "mowl", blocked, library_error=FakeMowlError
        )
    after = (await connections.list_connections(env.factory, ctx))[0]
    assert (after.status, after.last_error, after.last_used_at) == ("connected", None, None)
    assert (before.status, before.last_error) == (after.status, after.last_error)


async def test_connect_signs_in_first_and_never_keeps_the_password(
    env: Env, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    ctx = make_context(await env.user())
    ring = env.app.state.keyring

    def refused() -> dict[str, str]:
        password = PASSWORD
        raise FakeMowlError(f"login {password} failed, ticket {TICKET}") from HTTPError(
            f"https://mowl.example/ticket/{TICKET}",
            401,
            TICKET,
            None,  # pyright: ignore[reportArgumentType]
            None,
        )

    with pytest.raises(ConnectionFailure) as raised:
        await connections.connect(
            env.factory, ring, ctx, "mowl", refused, library_error=FakeMowlError
        )
    assert raised.value.args == ("invalid_credentials",)
    assert raised.value.__cause__ is None
    assert raised.value.__context__ is None

    def ticket_exchange_fails() -> dict[str, str]:
        raise FakeMowlError(f"exchange of ticket {TICKET} for {PASSWORD} refused")

    with pytest.raises(ConnectionFailure, match="invalid_credentials"):
        await connections.connect(
            env.factory, ring, ctx, "mowl", ticket_exchange_fails, library_error=FakeMowlError
        )
    assert await connections.list_connections(env.factory, ctx) == []

    row_id = await connections.connect(
        env.factory, ring, ctx, "mowl", lambda: {"token": TOKEN}, library_error=FakeMowlError
    )
    assert (await connections.list_connections(env.factory, ctx))[0].id == row_id
    for secret in (TOKEN, TICKET, PASSWORD):
        assert secret not in logged(caplog)


async def test_disconnect_and_account_deletion_remove_the_row(env: Env, database_url: str) -> None:
    ctx = await connected(env)
    assert await connections.disconnect(env.factory, ctx, "mowl") is True
    assert await connections.disconnect(env.factory, ctx, "mowl") is False
    await connections.save(env.factory, env.app.state.keyring, ctx, "mowl", {"token": TOKEN})
    await env.run("DELETE FROM users WHERE id = :u", u=ctx.user_id)
    admin = create_engine(database_url)
    try:
        with admin.connect() as conn:
            assert conn.execute(text("SELECT count(*) FROM connections")).scalar_one() == 0
    finally:
        admin.dispose()


async def test_payloads_are_checked(env: Env) -> None:
    ctx = make_context(await env.user())
    ring = env.app.state.keyring
    with pytest.raises(ValueError, match="fields"):
        await connections.save(env.factory, ring, ctx, "mowl", {"token": TOKEN, "extra": "x"})
    with pytest.raises(ValueError, match="provider"):
        await connections.save(env.factory, ring, ctx, "other", {"token": TOKEN})
    await connections.save(
        env.factory,
        ring,
        ctx,
        "spotify",
        {"access_token": "at-fake", "refresh_token": "rt-fake", "expires_at": 1.5},
        public={"client_id": "fake-client-id"},
    )
    opened = await connections.open_connection(env.factory, ring, ctx, "spotify")
    creds = opened.credentials
    assert (creds.spotify_client_id, creds.spotify_access_token, creds.spotify_expires_at) == (
        "fake-client-id",
        "at-fake",
        1.5,
    )
    assert "at-fake" not in repr(opened) and "rt-fake" not in repr(creds)  # noqa: PT018


def dump_database(dsn: str) -> tuple[list[str], bytes, bytes | None]:
    """The data of every table in ``public`` via ``COPY ... TO STDOUT`` (what ``pg_dump`` writes
    for data), as the superuser (no RLS); and ``pg_dump --data-only`` itself when a client of the
    server's major version is installed (``None`` otherwise)."""
    import shutil
    import subprocess

    import psycopg
    from psycopg import sql

    copied = bytearray()
    with psycopg.connect(dsn) as conn:
        tables = [
            r[0]
            for r in conn.execute("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")
        ]
        for table in tables:
            statement = sql.SQL("COPY public.{} TO STDOUT").format(sql.Identifier(table))
            with conn.cursor().copy(statement) as copy:
                for chunk in copy:
                    copied += bytes(chunk)
        server_major = conn.info.server_version // 10000
    pg_dump = shutil.which("pg_dump")
    dumped = None
    if pg_dump is not None:
        version = subprocess.run(  # noqa: S603 - the installed client, fixed arguments
            [pg_dump, "--version"], capture_output=True, text=True, check=False
        ).stdout
        if f" {server_major}." in version:
            dumped = subprocess.run(  # noqa: S603
                [pg_dump, "--data-only", dsn], capture_output=True, check=True
            ).stdout
    return tables, bytes(copied), dumped


async def test_a_dump_of_the_database_holds_ciphertext_only(env: Env, database_url: str) -> None:
    from sqlalchemy.engine import make_url

    ctx = await connected(env, token=TOKEN)
    await connections.save(
        env.factory,
        env.app.state.keyring,
        ctx,
        "spotify",
        {"access_token": "at-" + NEW_TOKEN, "refresh_token": "rt-" + NEW_TOKEN, "expires_at": 1},
    )
    await add_totp(env, env.app.state.keyring, ctx.user_id)
    dsn = make_url(database_url).set(drivername="postgresql").render_as_string(hide_password=False)
    tables, copied, dumped = await asyncio.to_thread(dump_database, dsn)
    assert "connections" in tables
    assert copied  # the rows are there, as ciphertext
    plain = (
        TOKEN,
        NEW_TOKEN,
        "at-" + NEW_TOKEN,
        "rt-" + NEW_TOKEN,
        TOKEN.replace("-", ""),
        TOTP_SECRET.decode(),
        bytes(range(32)).hex(),
    )
    # A bytea column is dumped as ``\x`` followed by hex, so a plaintext value would show as the
    # hex of its bytes (any case), not as itself.
    forms = [value.encode() for value in plain]
    forms += [value.encode().hex().encode() for value in plain]
    forms += [value.encode().hex().upper().encode() for value in plain]
    for output in (copied, dumped or b""):
        for form in forms:
            assert form not in output
