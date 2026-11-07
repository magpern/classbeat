# ruff: noqa: F811 - the race helpers' fixtures are imported and named as parameters
"""Admin disable and delete racing the session middleware's location event (#675, ADR 0019 §6).

``POST /api/admin/users/{id}/disable`` and ``DELETE /api/admin/users/{id}`` lock the target's
``users`` row and then delete every session of the target. The session middleware, on a request
from a new network, locks that request's session row and then inserts an ``auth_events`` row for
the user, whose foreign key takes ``FOR KEY SHARE`` on the same ``users`` row (#632). With the
admin holding ``FOR UPDATE`` (or the row lock of ``DELETE FROM users``) while it waits for that
session row, the two deadlock (#642 found the same with the reset). So the admin paths lock the
row ``FOR NO KEY UPDATE`` and delete the sessions before the ``users`` row.

Deterministic, in the style of ``test_auth_revoke_races``: the moved request parks between its
session ``UPDATE`` and its event insert; the admin call starts and must wait for a lock
(``pg_stat_activity``) before the event insert is let go. Both must then finish. The wait is read
as the superuser: the admin call runs as ``classbeat_maint``, and the app role does not see
another role's ``wait_event_type``.
"""

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any
from uuid import UUID

import httpx
import pytest
from sqlalchemy.engine import make_url

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from authtools import Env, running_env
from classbeat.auth.anomaly import EVENT_KIND
from flows import PASSWORD, user_with_password
from smtpfake import SmtpFake
from test_auth_reset_races import POLLS, At, own_client, park_location_event, sessions_of

USERS = "/api/admin/users"
EMAIL = "user@example.com"


@pytest.fixture
async def env(
    clean_auth: None, app_database_url: str, maint_database_url: str, smtp: SmtpFake
) -> AsyncIterator[Env]:
    """The admin area on ``classbeat_maint``, behind a trusted loopback proxy (so
    ``X-Forwarded-For`` counts and a request from another address is a location change)."""
    url = make_url(maint_database_url)
    async with running_env(
        app_database_url,
        maint_database_url=url.set(password=None).render_as_string(hide_password=False),
        database_maint_password=url.password,
        trusted_proxies="127.0.0.1",
        smtp_host="127.0.0.1",
        smtp_port=smtp.port,
        smtp_tls="none",
    ) as running:
        yield running


@pytest.fixture
async def probe(database_url: str) -> AsyncIterator[AsyncEngine]:
    """The superuser, to read every backend's lock wait."""
    engine = create_async_engine(database_url)
    try:
        yield engine
    finally:
        await engine.dispose()


_WAITING = text(
    "SELECT count(*) FROM pg_stat_activity WHERE wait_event_type = 'Lock' "
    "AND datname = current_database() AND pid <> pg_backend_pid() "
    "AND backend_type = 'client backend'"
)


async def until_it_waits(probe: AsyncEngine, task: asyncio.Future[Any]) -> None:
    """Return once some backend waits for a lock; fail if ``task`` ends without waiting."""
    for _ in range(POLLS):
        async with probe.connect() as connection:
            if (await connection.execute(_WAITING)).scalar_one():
                return
        if task.done():
            raise AssertionError(f"finished without waiting for a lock: {task}")
        await asyncio.sleep(0.01)
    raise AssertionError("never waited for a lock")


async def stepped_admin(env: Env, make_actor: Any) -> Any:
    """An admin with TOTP in the last 12 hours and a fresh step-up."""
    admin = await env.user("admin@example.com", role="admin")
    actor = await make_actor(admin, mfa=True)
    await env.run("UPDATE sessions SET stepup_at = now() WHERE user_id = :u", u=admin)
    return actor


async def two_sessions(env: Env) -> tuple[UUID, At, At]:
    """The target signed in twice from the home network: (user id, first, second)."""
    user_id = await user_with_password(env)
    first, second = At(env), At(env)
    assert (await first.sign_in(EMAIL, PASSWORD)).status_code == 200
    assert (await second.sign_in(EMAIL, PASSWORD)).status_code == 200
    return user_id, first, second


async def beside_a_location_event(
    env: Env,
    probe: AsyncEngine,
    monkeypatch: pytest.MonkeyPatch,
    moved: At,
    act: Callable[[], Awaitable[httpx.Response]],
) -> tuple[httpx.Response, httpx.Response]:
    """``moved`` requests from a new network and parks holding its session row, before the
    ``auth_events`` insert; ``act`` (the delete or disable) runs until it waits for a lock; then the
    insert goes on and both finish."""
    inside, release = park_location_event(monkeypatch)
    async with own_client(env) as a:
        moved.env = a
        moving = asyncio.ensure_future(moved.get("/api/auth/session"))
        await inside.wait()
        acting = asyncio.ensure_future(act())
        try:
            await until_it_waits(probe, acting)
        finally:
            release.set()
        response = await moving
        result = await acting
    return response, result


async def location_events(env: Env) -> object:
    return await env.scalar("SELECT count(*) FROM auth_events WHERE kind = :k", k=EVENT_KIND)


async def test_admin_disable_and_a_location_event_both_succeed(
    env: Env, probe: AsyncEngine, make_actor: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    actor = await stepped_admin(env, make_actor)
    user_id, first, _ = await two_sessions(env)
    moved, disabled = await beside_a_location_event(
        env,
        probe,
        monkeypatch,
        first.elsewhere(env),
        lambda: actor.post(f"{USERS}/{user_id}/disable"),
    )
    assert (moved.status_code, disabled.status_code) == (200, 200), disabled.text  # no deadlock
    assert disabled.json()["sessions_revoked"] == 2
    assert await sessions_of(env, user_id) == 0
    assert await env.scalar("SELECT disabled_at FROM users WHERE id = :u", u=user_id) is not None
    assert await location_events(env) == 1


async def test_admin_delete_and_a_location_event_both_succeed(
    env: Env, probe: AsyncEngine, make_actor: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    actor = await stepped_admin(env, make_actor)
    user_id, first, _ = await two_sessions(env)
    moved, deleted = await beside_a_location_event(
        env,
        probe,
        monkeypatch,
        first.elsewhere(env),
        lambda: actor.call("DELETE", f"{USERS}/{user_id}", {"email": EMAIL}),
    )
    assert (moved.status_code, deleted.status_code) == (200, 204), deleted.text  # no deadlock
    assert await sessions_of(env, user_id) == 0
    assert await env.scalar("SELECT count(*) FROM users WHERE id = :u", u=user_id) == 0


async def test_the_users_own_delete_and_a_location_event_both_succeed(
    env: Env, probe: AsyncEngine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``POST /api/account/delete`` goes through the same ``delete_account`` (no user lock: its
    ``DELETE FROM users`` alone deadlocked the same way before the sessions went first)."""
    user_id, first, second = await two_sessions(env)
    moved, deleted = await beside_a_location_event(
        env,
        probe,
        monkeypatch,
        first.elsewhere(env),
        lambda: second.post("/api/account/delete", {"confirm_email": EMAIL}),
    )
    assert (moved.status_code, deleted.status_code) == (200, 204), deleted.text  # no deadlock
    assert await sessions_of(env, user_id) == 0
    assert await env.scalar("SELECT count(*) FROM users WHERE id = :u", u=user_id) == 0
