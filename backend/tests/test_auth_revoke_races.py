"""Sign out everywhere and the two MFA resets racing a sign-in (#643, ADR 0019 section 6).

``POST /api/auth/logout-all``, ``admin mfa-reset`` (``accounts.reset_mfa``) and the TOTP reset of
``keys wipe`` (``operator.wipe_user``) revoke every session of a user. Each takes the ``users``
row lock (``FOR NO KEY UPDATE``) before it deletes anything, so a sign-in committing at the same
moment is waited for and its session is revoked too, instead of surviving a "revoke everything".

Deterministic, in the style of ``test_auth_reset_races``: the sign-in parks inside its
transaction, holding the user row lock with its new session uncommitted; the revoke starts and
must wait for a lock (``pg_stat_activity``) before the sign-in is let go. Without the lock the
revoke's ``DELETE FROM sessions`` does not see the uncommitted row and finishes without waiting.

The second half: each revoke racing the session middleware's location event (a session row
locked, an ``auth_events`` insert for the user pending) finishes on both sides, with no deadlock.
That is what ``FOR UPDATE`` instead of ``FOR NO KEY UPDATE`` would break (#639 review).
"""

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar
from uuid import UUID

import httpx
import pytest

from authtools import Env
from classbeat import auth
from classbeat.auth import signin_routes, totp
from classbeat.auth.anomaly import EVENT_KIND
from flows import PASSWORD, Browser, user_with_password
from mfatools import step_of
from test_auth_reset_races import (  # noqa: F401  (penv is a fixture the tests name)
    At,
    own_client,
    park_location_event,
    parked,
    penv,
    sessions_of,
    until_it_waits,
)

EMAIL = "user@example.com"
T = TypeVar("T")


async def enroll(browser: Browser) -> None:
    """Enroll TOTP for the browser's user (its sign-in is recent enough to do so)."""
    started = await browser.post("/api/auth/mfa/totp/start")
    assert started.status_code == 200, started.text
    body = started.json()
    code = totp.code_for_step(body["secret"], step_of(totp.clock()))
    done = await browser.post(
        "/api/auth/mfa/totp/confirm", {"pending": body["pending"], "code": code}
    )
    assert done.status_code == 200, done.text


async def totp_rows(env: Env, user_id: UUID) -> int:
    return int(await env.scalar("SELECT count(*) FROM mfa_totp WHERE user_id = :u", u=user_id))  # type: ignore[arg-type]


async def reset_mfa(env: Env) -> None:
    """``admin mfa-reset``: the CLI calls this in its own system session."""
    async with env.db() as db:
        await auth.reset_mfa(db, EMAIL)


async def wipe_totp(env: Env, user_id: UUID) -> None:
    """The ``keys wipe`` step for a user with TOTP and no connection left to delete."""
    async with env.db() as db:
        event = await auth.wipe_user(db, user_id, [])
    assert event is not None
    assert event.totp_reset


async def a_sign_in_parked_against(
    env: Env, monkeypatch: pytest.MonkeyPatch, revoke: Callable[[Env], Awaitable[T]]
) -> T:
    """A password sign-in parks after its session row is inserted (uncommitted, under the user
    row lock); ``revoke`` must wait for a lock before the sign-in goes on. Returns its result."""
    inside, release = parked(monkeypatch, signin_routes, "remember_device")
    async with own_client(env) as a, own_client(env) as b:
        browser = Browser(a)
        signing_in = asyncio.ensure_future(browser.sign_in(EMAIL, PASSWORD))
        await inside.wait()
        revoking = asyncio.ensure_future(revoke(b))
        try:
            await until_it_waits(env, revoking)
        finally:
            release.set()
        signed, revoked = await asyncio.gather(signing_in, revoking)
    assert signed.status_code == 200  # the sign-in did get its session, and no deadlock error
    assert browser.signed_in
    return revoked


# --- a sign-in committing during the revoke loses its session ---------------------------------


async def test_a_sign_in_inside_its_transaction_makes_sign_out_everywhere_wait(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    user_id = await user_with_password(env)
    here = Browser(env)
    assert (await here.sign_in(EMAIL)).status_code == 200

    async def logout_all(other: Env) -> httpx.Response:
        here.env = other
        return await here.post("/api/auth/logout-all")

    response = await a_sign_in_parked_against(env, monkeypatch, logout_all)
    assert response.status_code == 204
    assert await sessions_of(env, user_id) == 0  # the parked sign-in's session went too


async def test_a_sign_in_inside_its_transaction_makes_the_mfa_reset_wait(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    user_id = await user_with_password(env)
    here = Browser(env)
    assert (await here.sign_in(EMAIL)).status_code == 200
    await enroll(here)
    await a_sign_in_parked_against(env, monkeypatch, reset_mfa)
    assert await sessions_of(env, user_id) == 0
    assert await totp_rows(env, user_id) == 0


async def test_a_sign_in_inside_its_transaction_makes_the_keys_wipe_totp_reset_wait(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    user_id = await user_with_password(env)
    here = Browser(env)
    assert (await here.sign_in(EMAIL)).status_code == 200
    await enroll(here)
    await a_sign_in_parked_against(env, monkeypatch, lambda other: wipe_totp(other, user_id))
    assert await sessions_of(env, user_id) == 0
    assert await totp_rows(env, user_id) == 0


# --- the session middleware's location event does not deadlock with the lock ------------------


async def beside_a_location_event(
    env: Env, monkeypatch: pytest.MonkeyPatch, moved: At, act: Callable[[Env], Awaitable[T]]
) -> tuple[httpx.Response, T]:
    """``moved`` requests from a new network and parks holding its session row, before the
    ``auth_events`` insert; ``act`` runs until it waits for that row while holding the user row
    lock; then the insert goes on. With ``FOR UPDATE`` the two would deadlock."""
    inside, release = park_location_event(monkeypatch)
    async with own_client(env) as a, own_client(env) as b:
        moved.env = a
        moving = asyncio.ensure_future(moved.get("/api/auth/session"))
        await inside.wait()
        acting = asyncio.ensure_future(act(b))
        try:
            await until_it_waits(env, acting)
        finally:
            release.set()
        response = await moving
        result = await acting
    return response, result


async def two_sessions(env: Env) -> tuple[UUID, At, At]:
    """A user signed in twice from the home network: (user id, first, second)."""
    user_id = await user_with_password(env)
    first, second = At(env), At(env)
    assert (await first.sign_in(EMAIL, PASSWORD)).status_code == 200
    assert (await second.sign_in(EMAIL, PASSWORD)).status_code == 200
    return user_id, first, second


async def location_events(env: Env) -> object:
    return await env.scalar("SELECT count(*) FROM auth_events WHERE kind = :k", k=EVENT_KIND)


async def test_sign_out_everywhere_and_a_location_event_both_succeed(
    penv: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    user_id, first, second = await two_sessions(penv)

    async def logout_all(other: Env) -> httpx.Response:
        second.env = other
        return await second.post("/api/auth/logout-all")

    moved, response = await beside_a_location_event(
        penv, monkeypatch, first.elsewhere(penv), logout_all
    )
    assert (moved.status_code, response.status_code) == (200, 204)  # no deadlock, no 500
    assert await sessions_of(penv, user_id) == 0
    assert await location_events(penv) == 1


async def test_the_mfa_reset_and_a_location_event_both_succeed(
    penv: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    user_id, first, second = await two_sessions(penv)
    await enroll(second)
    moved, _ = await beside_a_location_event(penv, monkeypatch, first.elsewhere(penv), reset_mfa)
    assert moved.status_code == 200
    assert await sessions_of(penv, user_id) == 0
    assert await totp_rows(penv, user_id) == 0
    assert await location_events(penv) == 1


async def test_the_keys_wipe_totp_reset_and_a_location_event_both_succeed(
    penv: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    user_id, first, second = await two_sessions(penv)
    await enroll(second)
    moved, _ = await beside_a_location_event(
        penv, monkeypatch, first.elsewhere(penv), lambda other: wipe_totp(other, user_id)
    )
    assert moved.status_code == 200
    assert await sessions_of(penv, user_id) == 0
    assert await totp_rows(penv, user_id) == 0
    assert await location_events(penv) == 1
