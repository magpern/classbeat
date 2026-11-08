"""The real reset racing a passkey register, sign-in and re-auth finish (ADR 0019 sections 6 and 9,
#775).

``test_passkey_locks`` plays the reset with raw statements; here the reset is the real
``POST /password/reset/complete``. Deterministic, not timed: the test holds the ``users`` row lock,
starts the first request and waits until Postgres reports it waiting, then the second likewise,
and releases the lock. Postgres hands a row lock to its waiters in the order they queued (the
first holds the tuple lock the second waits for), so each test runs both orders: the finish
committed before the reset (and is undone by it), or the reset committed before the finish (which
then finds its session or passkey gone). Either way nothing made by the finish outlives the reset
and no request fails with a deadlock.
"""

import asyncio
from collections.abc import Awaitable
from typing import Any
from uuid import UUID

import httpx
import pytest
from sqlalchemy import text

from authtools import Env
from classbeat import auth
from flows import Browser
from passkeysignin import SESSIONS, finish_signin
from passkeytools import EMAIL, count, finish, reason, register, signed_in, start, user_id_of
from passkeytools import penv as penv
from smtpfake import SmtpFake
from test_auth_reset_races import POLLS, own_client, waiting_for_a_lock
from test_passkey_locks import REAUTHED, reauth_ready, signin_ready
from test_passkey_reauth import finish as reauth_finish
from webauthnfake import SoftAuthenticator

pytestmark = pytest.mark.usefixtures("frozen_rate_windows")

NEW = "a brand new passphrase"
CREDENTIALS = "SELECT count(*) FROM webauthn_credentials"
PASSKEY_SIGN_INS = (
    "SELECT count(*) FROM auth_events WHERE kind = 'sign_in' AND detail->>'method' = 'passkey'"
)
ORDERS = pytest.mark.parametrize("finish_first", [True, False], ids=["finish_first", "reset_first"])


async def until_waiting(env: Env, waiters: int, task: asyncio.Future[Any]) -> None:
    """Return once ``waiters`` backends wait for a lock; fail if ``task`` ends without waiting."""
    for _ in range(POLLS):
        if await waiting_for_a_lock(env) >= waiters:
            return
        if task.done():
            raise AssertionError(f"finished without waiting for the user row lock: {task}")
        await asyncio.sleep(0.01)
    raise AssertionError("never waited for a lock")


async def in_lock_order(
    env: Env, user_id: UUID, *requests: Awaitable[httpx.Response]
) -> list[httpx.Response]:
    """Hold the user row lock, queue ``requests`` on it one after the other, then release it."""
    tasks: list[asyncio.Future[httpx.Response]] = []
    try:
        async with env.db() as db:
            await db.execute(
                text("SELECT id FROM users WHERE id = :u FOR NO KEY UPDATE"), {"u": user_id}
            )
            for queued, request in enumerate(requests, start=1):
                tasks.append(asyncio.ensure_future(request))
                await until_waiting(env, queued, tasks[-1])
    finally:
        pending = [task for task in tasks if not task.done()]
        if pending:
            await asyncio.wait(pending)  # the lock is released: let them finish
    return [await task for task in tasks]


async def reset_token(env: Env, smtp: SmtpFake) -> str:
    asked = await Browser(env).post(
        "/api/auth/password/reset/request", {"email": "user@example.com"}
    )
    assert asked.status_code == 202
    return [m for m in smtp.messages if "#token=" in m.text][-1].token()


async def complete(env: Env, token: str, keep: list[str]) -> httpx.Response:
    """The real reset in a client of its own (parallel requests never share a cookie jar)."""
    async with own_client(env) as own:
        body = {"token": token, "password": NEW, "keep_methods": [], "keep_passkeys": keep}
        return await Browser(own).post("/api/auth/password/reset/complete", body)


def ordered(
    finish_first: bool, finishing: Awaitable[httpx.Response], resetting: Awaitable[httpx.Response]
) -> tuple[list[Awaitable[httpx.Response]], int]:
    """The requests in queue order, and the index of the finish's response."""
    return ([finishing, resetting], 0) if finish_first else ([resetting, finishing], 1)


# --- reset versus register ---------------------------------------------------------------------


@ORDERS
async def test_a_register_finish_racing_a_reset_leaves_only_the_kept_passkey(
    penv: Env, smtp: SmtpFake, finish_first: bool
) -> None:
    """The user kept the passkey the reset page showed; one being registered meanwhile is either
    stored and then deleted by the reset (not on the keep list), or never stored (its session is
    gone when the finish gets the lock)."""
    client = await signed_in(penv)
    assert (await register(client, SoftAuthenticator())).status_code == 201
    user = await user_id_of(penv)
    shown = str(await penv.scalar("SELECT id FROM webauthn_credentials"))
    token = await reset_token(penv, smtp)
    credential = SoftAuthenticator().create((await start(client)).json())
    requests, at = ordered(finish_first, finish(client, credential), complete(penv, token, [shown]))
    responses = await in_lock_order(penv, user, *requests)
    registered, reset = responses[at], responses[1 - at]
    assert reset.status_code == 200, reset.text
    if finish_first:
        assert registered.status_code == 201, registered.text
    else:
        assert (registered.status_code, reason(registered)) == (400, "passkey_failed")
    assert str(await penv.scalar("SELECT id FROM webauthn_credentials")) == shown
    assert await count(penv, CREDENTIALS) == 1
    assert await count(penv, SESSIONS) == 0
    removed = 1 if finish_first else 0
    detail = await penv.scalar("SELECT detail FROM auth_events WHERE kind = 'password_reset'")
    assert detail == {"methods_removed": 0, "passkeys_removed": removed}


# --- reset versus sign-in ----------------------------------------------------------------------


@ORDERS
async def test_a_signin_finish_racing_a_reset_that_drops_its_passkey_leaves_no_session(
    penv: Env, smtp: SmtpFake, finish_first: bool
) -> None:
    holder, client, assertion = await signin_ready(penv)
    token = await reset_token(penv, smtp)
    requests, at = ordered(
        finish_first, finish_signin(client, assertion), complete(penv, token, [])
    )
    responses = await in_lock_order(penv, holder.user_id, *requests)
    signed, reset = responses[at], responses[1 - at]
    assert reset.status_code == 200, reset.text
    if finish_first:
        assert signed.status_code == 200, signed.text  # signed in, then signed out by the reset
    else:
        assert (signed.status_code, reason(signed)) == (400, "passkey_failed")
        assert await count(penv, PASSKEY_SIGN_INS) == 0
    assert await count(penv, SESSIONS) == 0
    assert await count(penv, CREDENTIALS) == 0
    assert (await client.get("/api/auth/session")).json()["authenticated"] is False


# --- reset versus re-auth ----------------------------------------------------------------------


@ORDERS
async def test_a_reauth_finish_racing_a_reset_that_drops_its_passkey_leaves_no_session(
    penv: Env, smtp: SmtpFake, finish_first: bool
) -> None:
    client, user, assertion = await reauth_ready(penv)
    token = await reset_token(penv, smtp)
    requests, at = ordered(
        finish_first, reauth_finish(client, assertion), complete(penv, token, [])
    )
    responses = await in_lock_order(penv, user, *requests)
    reauthed, reset = responses[at], responses[1 - at]
    assert reset.status_code == 200, reset.text
    if finish_first:
        assert reauthed.status_code == 200, reauthed.text  # rotated, then revoked by the reset
    else:
        assert (reauthed.status_code, reason(reauthed)) == (400, "passkey_failed")
        assert await count(penv, REAUTHED) == 0
    assert await count(penv, SESSIONS) == 0  # no rotated session outlives the reset
    assert await count(penv, CREDENTIALS) == 0
    assert (await client.get("/api/auth/session")).json()["authenticated"] is False


async def test_mfa_reset_deletes_passkeys_only_under_the_user_row_lock(penv: Env) -> None:
    """``admin mfa-reset`` (``accounts.reset_mfa``) takes the user row lock before its passkey
    delete (#785 review N1): a register finish that holds the lock with its passkey inserted but
    not yet committed is waited for, and that passkey is deleted too. A delete before the lock
    would not see the uncommitted row, and the passkey would outlive the reset."""
    await signed_in(penv)
    user_id = await user_id_of(penv)
    resetting: asyncio.Future[None] | None = None

    async def reset_mfa() -> None:
        async with penv.db() as db:
            await auth.reset_mfa(db, EMAIL)

    try:
        async with penv.db() as db:  # what a register finish holds before it commits
            await db.execute(
                text("SELECT id FROM users WHERE id = :u FOR NO KEY UPDATE"), {"u": user_id}
            )
            await db.execute(
                text(
                    "INSERT INTO webauthn_credentials (user_id, credential_id, public_key, "
                    "backup_eligible, backup_state, label, transports) "
                    "VALUES (:u, :c, :k, true, true, 'Racing', '{internal}')"
                ),
                {"u": user_id, "c": b"racing-credential", "k": b"\xa1\x01\x02"},
            )
            resetting = asyncio.ensure_future(reset_mfa())
            await until_waiting(penv, 1, resetting)
    finally:
        if resetting is not None and not resetting.done():
            await asyncio.wait([resetting])
    assert resetting is not None
    await resetting
    assert await count(penv, CREDENTIALS) == 0
