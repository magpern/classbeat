"""A password reset racing a sign-in, a re-authentication or a link (#639, ADR 0019 section 6).

Every finish that creates or rotates a session or links an identity locks the ``users`` row
first and re-checks what it proved under that lock; the reset locks it right after its token.
These tests are deterministic, not timed: each one parks one side inside its transaction (an
``asyncio.Event``) or holds the row lock itself, waits until Postgres reports the other side
waiting for a lock (``pg_stat_activity``), and only then lets the first one go.
"""

import asyncio
import dataclasses
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Any
from uuid import UUID

import httpx
import pytest
from sqlalchemy import text

from authtools import ORIGIN, Env, running_env
from classbeat.auth import reset_routes, signin_routes, totp
from classbeat.auth import sessions as sessions_module
from classbeat.auth.anomaly import EVENT_KIND
from flows import PASSWORD, Browser, reason, signed_in, user_with_password
from mfatools import admin_with_totp, step_of
from oauthtools import location, sign_in_with, start
from oidcfake import FakeProviders
from smtpfake import SmtpFake

EMAIL = "user@example.com"
NEW = "a brand new passphrase"
POLLS = 1000  # x 10 ms: a generous cap for a slow runner; the tests never sleep to order steps


@asynccontextmanager
async def own_client(env: Env) -> AsyncIterator[Env]:
    """The env with a client of its own, so parallel requests never share a cookie jar."""
    transport = httpx.ASGITransport(app=env.app)
    async with httpx.AsyncClient(transport=transport, base_url=ORIGIN) as client:
        yield dataclasses.replace(env, client=client)


async def waiting_for_a_lock(env: Env) -> int:
    return int(
        await env.scalar(  # type: ignore[arg-type]
            "SELECT count(*) FROM pg_stat_activity WHERE wait_event_type = 'Lock' "
            "AND datname = current_database() AND pid <> pg_backend_pid() "
            "AND backend_type = 'client backend'"
        )
    )


async def until_it_waits(env: Env, task: asyncio.Future[Any]) -> None:
    """Return once some backend waits for a lock; fail if ``task`` ends without waiting."""
    for _ in range(POLLS):
        if await waiting_for_a_lock(env):
            return
        if task.done():
            raise AssertionError(f"finished without waiting for the user row lock: {task}")
        await asyncio.sleep(0.01)
    raise AssertionError("never waited for a lock")


async def against_a_held_lock(
    env: Env, user_id: UUID, request: Awaitable[httpx.Response], *reset: str
) -> httpx.Response:
    """Play the reset: hold the user row lock, let ``request`` run until it waits for a lock,
    then run the ``reset`` statements (what a reset deletes) and commit."""
    task = None
    try:
        async with env.db() as db:
            await db.execute(
                text("SELECT id FROM users WHERE id = :u FOR NO KEY UPDATE"), {"u": user_id}
            )
            task = asyncio.ensure_future(request)
            await until_it_waits(env, task)
            for statement in reset:
                await db.execute(text(statement), {"u": user_id})
    finally:
        if task is not None and not task.done():
            await asyncio.wait([task])  # the lock is released: let it finish
    assert task is not None
    return await task


def parked(monkeypatch: pytest.MonkeyPatch, module: object, name: str):
    """Wrap ``module.name`` so its call returns, then parks until ``release`` is set."""
    inside, release = asyncio.Event(), asyncio.Event()
    original: Callable[..., Awaitable[Any]] = getattr(module, name)

    async def wrapper(*args: Any, **kwargs: Any) -> Any:
        result = await original(*args, **kwargs)
        inside.set()
        await release.wait()
        return result

    monkeypatch.setattr(module, name, wrapper)
    return inside, release


async def reset_token(env: Env, smtp: SmtpFake) -> str:
    asked = await Browser(env).post("/api/auth/password/reset/request", {"email": EMAIL})
    assert asked.status_code == 202
    return smtp.to(EMAIL)[-1].token()


async def complete(env: Env, token: str) -> httpx.Response:
    return await Browser(env).post(
        "/api/auth/password/reset/complete",
        {"token": token, "password": NEW, "keep_methods": []},
    )


async def sessions_of(env: Env, user_id: UUID) -> int:
    return int(await env.scalar("SELECT count(*) FROM sessions WHERE user_id = :u", u=user_id))  # type: ignore[arg-type]


# --- password sign-in vs reset (issue window 1) ---------------------------------------------


async def test_a_reset_committed_after_the_password_check_stops_the_sign_in(
    menv: Env, smtp: SmtpFake, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The sign-in verified the old password; the reset commits before its session starts."""
    user_id = await user_with_password(menv)
    token = await reset_token(menv, smtp)
    hasher = menv.app.state.password_hasher
    original, resets = hasher.verify, []

    async def verify_then_reset(stored: str, typed: str) -> bool:
        matched = await original(stored, typed)
        if matched and not resets:
            async with own_client(menv) as other:
                resets.append(await complete(other, token))
        return matched

    monkeypatch.setattr(hasher, "verify", verify_then_reset)
    browser = Browser(menv)
    signed = await browser.sign_in(EMAIL, PASSWORD)
    assert [r.status_code for r in resets] == [200]
    assert (signed.status_code, reason(signed)) == (401, "invalid_credentials")
    assert not browser.signed_in
    assert await sessions_of(menv, user_id) == 0
    assert await menv.scalar("SELECT detail FROM auth_events WHERE kind = 'sign_in_failed'") == {
        "why": "changed"
    }
    monkeypatch.setattr(hasher, "verify", original)
    assert (await Browser(menv).sign_in(EMAIL, NEW)).status_code == 200


async def test_a_sign_in_inside_its_transaction_makes_the_reset_wait_then_loses_its_session(
    menv: Env, smtp: SmtpFake, monkeypatch: pytest.MonkeyPatch
) -> None:
    user_id = await user_with_password(menv)
    token = await reset_token(menv, smtp)
    inside, release = parked(monkeypatch, signin_routes, "remember_device")
    async with own_client(menv) as a, own_client(menv) as b:
        browser = Browser(a)
        signing_in = asyncio.ensure_future(browser.sign_in(EMAIL, PASSWORD))
        await inside.wait()  # its session row exists, uncommitted, under the user row lock
        resetting = asyncio.ensure_future(complete(b, token))
        try:
            await until_it_waits(menv, resetting)
        finally:
            release.set()
        signed, reset = await asyncio.gather(signing_in, resetting)
    assert (signed.status_code, reset.status_code) == (200, 200)  # no deadlock error
    assert browser.signed_in  # it did get a session ...
    assert await sessions_of(menv, user_id) == 0  # ... which the reset saw and revoked
    browser.env = menv
    assert not (await browser.get("/api/auth/session")).json()["authenticated"]


async def test_a_reset_inside_its_transaction_makes_the_sign_in_wait_then_fail(
    menv: Env, smtp: SmtpFake, monkeypatch: pytest.MonkeyPatch
) -> None:
    user_id = await user_with_password(menv)
    token = await reset_token(menv, smtp)
    inside, release = parked(monkeypatch, reset_routes, "forget_devices")
    async with own_client(menv) as a, own_client(menv) as b:
        resetting = asyncio.ensure_future(complete(a, token))
        await inside.wait()  # the new hash is written, uncommitted, under the user row lock
        browser = Browser(b)
        signing_in = asyncio.ensure_future(browser.sign_in(EMAIL, PASSWORD))
        try:
            await until_it_waits(menv, signing_in)  # checked the old hash, waits for the row
        finally:
            release.set()
        reset, signed = await asyncio.gather(resetting, signing_in)
    assert reset.status_code == 200
    assert (signed.status_code, reason(signed)) == (401, "invalid_credentials")
    assert await sessions_of(menv, user_id) == 0


# --- rotations wait for the reset instead of deadlocking --------------------------------------

REVOKE = "DELETE FROM sessions WHERE user_id = :u"


async def test_a_password_reauth_waits_for_a_reset_and_finds_its_session_gone(menv: Env) -> None:
    browser = await signed_in(menv)
    user_id = await menv.scalar("SELECT id FROM users")
    async with own_client(menv) as other:
        browser.env = other
        response = await against_a_held_lock(
            menv,
            user_id,  # type: ignore[arg-type]
            browser.post("/api/auth/reauth", {"password": PASSWORD}),
            REVOKE,
        )
    assert (response.status_code, reason(response)) == (401, "not_authenticated")
    assert await sessions_of(menv, user_id) == 0  # type: ignore[arg-type]


async def test_an_mfa_verify_waits_for_a_reset_and_finds_its_session_gone(
    env: Env, make_actor: Any, clock: list[float]
) -> None:
    _, secret, _, user_id = await admin_with_totp(env, make_actor)
    actor = await make_actor(user_id)  # a password-only session
    clock[0] += 60
    code = totp.code_for_step(secret, step_of(clock[0]))
    response = await against_a_held_lock(
        env, user_id, actor.post("/api/auth/mfa/verify", {"code": code}), REVOKE
    )
    assert (response.status_code, reason(response)) == (401, "not_authenticated")
    assert await sessions_of(env, user_id) == 0


# --- OAuth: link, reauth and sign-in vs reset (issue window 2) ---------------------------------


async def test_a_link_waiting_for_a_reset_links_nothing(oenv: Env, fake: FakeProviders) -> None:
    """The reset revokes the linking session while the callback waits: nothing is linked."""
    browser = await signed_in(oenv)
    user_id = await oenv.scalar("SELECT id FROM users")
    started = await start(browser, "google", "link")
    callback = fake.complete(started.json()["authorization_url"], sub="planted")
    async with own_client(oenv) as other:
        browser.env = other
        response = await against_a_held_lock(oenv, user_id, browser.get(callback), REVOKE)  # type: ignore[arg-type]
    assert location(response) == "/account?oauth=session_mismatch"
    assert await oenv.scalar("SELECT count(*) FROM identities") == 0


async def test_an_oauth_reauth_waits_for_a_reset_and_finds_its_session_gone(
    oenv: Env, fake: FakeProviders
) -> None:
    browser = await signed_in(oenv)
    user_id = await oenv.scalar("SELECT id FROM users")
    assert location(await sign_in_with(browser, fake, "github", purpose="link")) == (
        "/account?oauth=linked"
    )
    started = await start(browser, "github", "reauth")
    callback = fake.complete(started.json()["authorization_url"])
    async with own_client(oenv) as other:
        browser.env = other
        response = await against_a_held_lock(oenv, user_id, browser.get(callback), REVOKE)  # type: ignore[arg-type]
    assert location(response) == "/account?oauth=session_mismatch"
    assert await sessions_of(oenv, user_id) == 0  # type: ignore[arg-type]


async def test_an_oauth_sign_in_with_an_identity_the_reset_removes_gets_no_session(
    oenv: Env, fake: FakeProviders
) -> None:
    user_id = await user_with_password(oenv)
    await oenv.run(
        "INSERT INTO identities (user_id, provider, subject) VALUES (:u, 'google', 'gone')",
        u=user_id,
    )
    browser = Browser(oenv)
    started = await start(browser, "google")
    callback = fake.complete(started.json()["authorization_url"], sub="gone")
    async with own_client(oenv) as other:
        browser.env = other
        response = await against_a_held_lock(
            oenv,
            user_id,
            browser.get(callback),
            "DELETE FROM identities WHERE user_id = :u",
            REVOKE,
        )
    assert location(response) == "/signin?oauth=invalid"
    assert await sessions_of(oenv, user_id) == 0
    assert not browser.signed_in


# --- the session middleware's location event does not deadlock with the lock (#639 review) ---

HOME, MOBILE = "198.51.100.7", "203.0.113.40"


@pytest.fixture
async def penv(clean_auth: None, app_database_url: str, smtp: SmtpFake) -> AsyncIterator[Env]:
    """Mail to the SMTP fake, behind a trusted loopback proxy (so ``X-Forwarded-For`` counts)."""
    async with running_env(
        app_database_url,
        trusted_proxies="127.0.0.1",
        smtp_host="127.0.0.1",
        smtp_port=smtp.port,
        smtp_tls="none",
    ) as running:
        yield running


class At(Browser):
    """A browser at a client address: a request from another network is a location change."""

    def __init__(self, env: Env, ip: str = HOME) -> None:
        super().__init__(env)
        self.at = ip

    def _cookie_header(self) -> dict[str, str]:
        return {**super()._cookie_header(), "X-Forwarded-For": self.at}

    def elsewhere(self, env: Env) -> "At":
        twin = At(env, MOBILE)
        twin.cookies = dict(self.cookies)
        return twin


def park_location_event(monkeypatch: pytest.MonkeyPatch):
    """Park the middleware between its session ``UPDATE`` (the row stays locked) and its
    ``auth_events`` insert, whose foreign key takes ``FOR KEY SHARE`` on the user row."""
    inside, release = asyncio.Event(), asyncio.Event()
    original = sessions_module.record_event

    async def wrapper(db: Any, kind: str, **kwargs: Any) -> None:
        if kind == EVENT_KIND:
            inside.set()
            await release.wait()
        await original(db, kind, **kwargs)

    monkeypatch.setattr(sessions_module, "record_event", wrapper)
    return inside, release


async def against_a_location_event(
    env: Env,
    monkeypatch: pytest.MonkeyPatch,
    moved: At,
    other: Callable[[Env], Awaitable[httpx.Response]],
) -> tuple[httpx.Response, httpx.Response]:
    """``moved`` requests from a new network and parks holding its session row; ``other`` runs
    until it waits for that row while holding the user lock; then the event insert goes on."""
    inside, release = park_location_event(monkeypatch)
    async with own_client(env) as a, own_client(env) as b:
        moved.env = a
        moving = asyncio.ensure_future(moved.get("/api/auth/session"))
        await inside.wait()
        acting = asyncio.ensure_future(other(b))
        try:
            await until_it_waits(env, acting)
        finally:
            release.set()
        got = await asyncio.gather(moving, acting)
    return got[0], got[1]


async def test_a_reset_and_a_location_event_on_one_of_its_sessions_both_succeed(
    penv: Env, smtp: SmtpFake, monkeypatch: pytest.MonkeyPatch
) -> None:
    user_id = await user_with_password(penv)
    home = At(penv)
    assert (await home.sign_in(EMAIL, PASSWORD)).status_code == 200
    token = await reset_token(penv, smtp)
    moved, reset = await against_a_location_event(
        penv, monkeypatch, home.elsewhere(penv), lambda env: complete(env, token)
    )
    assert (moved.status_code, reset.status_code) == (200, 200)  # no deadlock, no 500
    assert await sessions_of(penv, user_id) == 0
    assert await penv.scalar("SELECT count(*) FROM auth_events WHERE kind = :k", k=EVENT_KIND) == 1


async def test_a_reauth_and_a_location_event_on_its_session_both_succeed(
    penv: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    user_id = await user_with_password(penv)
    home = At(penv)
    assert (await home.sign_in(EMAIL, PASSWORD)).status_code == 200
    moved_twin = home.elsewhere(penv)

    async def reauth(env: Env) -> httpx.Response:
        home.env = env
        return await home.post("/api/auth/reauth", {"password": PASSWORD})

    moved, reauthed = await against_a_location_event(penv, monkeypatch, moved_twin, reauth)
    assert moved.status_code == 200
    assert reauthed.status_code == 200  # no deadlock: it waited, then rotated the session
    assert await penv.scalar("SELECT count(*) FROM auth_events WHERE kind = :k", k=EVENT_KIND) == 1
    assert await sessions_of(penv, user_id) == 1
