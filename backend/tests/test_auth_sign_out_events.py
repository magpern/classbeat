"""Sign-out writes an ``auth_events`` row (#646, ADR 0003 amendment of 2026-11-06).

``POST /api/auth/logout`` writes one ``sign_out`` (detail: the session id) and
``POST /api/auth/logout-all`` one ``sign_out_all`` (detail: how many sessions ended), in the
revoke's transaction and only when the revoke deleted something. A failed insert never keeps the
session alive (a savepoint), and the sign-out takes the user row lock first, so it cannot
deadlock with an account deletion.
"""

import asyncio
import logging
from typing import Any
from uuid import UUID

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from authtools import Env, session_cookie, unsafe_headers
from classbeat import auth
from classbeat.auth import routes
from classbeat.auth.cookies import CSRF_COOKIE
from classbeat.auth.export import account_export
from test_auth_reset_races import own_client, parked, until_it_waits

pytestmark = pytest.mark.usefixtures("frozen_rate_windows")

CLIENT_IP = "127.0.0.1"  # httpx's ASGITransport peer
KINDS = ("sign_out", "sign_out_all")


async def csrf_of(env: Env, token: str) -> str:
    response = await env.client.get("/api/auth/session", headers=session_cookie(token))
    return response.json()["csrf_token"]


async def events(env: Env) -> list[Any]:
    async with env.db() as db:
        result = await db.execute(
            text(
                "SELECT kind, user_id, ip, detail FROM auth_events "
                "WHERE kind IN ('sign_out', 'sign_out_all') ORDER BY at, id"
            )
        )
        return list(result.all())


async def sign_out(env: Env, path: str, token: str, csrf: str) -> httpx.Response:
    return await env.client.post(path, headers=unsafe_headers(token, csrf))


async def sign_out_again(env: Env, path: str, token: str) -> httpx.Response:
    """The same browser repeats the request with its dead session cookie: the page asks for the
    CSRF token again and gets the anonymous double-submit one, so the request passes the checks
    and reaches the route as anonymous."""
    anonymous = "a" * 43
    headers = unsafe_headers(csrf=anonymous)
    headers["Cookie"] = f"{auth.SESSION_COOKIE}={token}; {CSRF_COOKIE}={anonymous}"
    return await env.client.post(path, headers=headers)


# --- one event per sign-out, none for a repeat -------------------------------------------------


async def test_sign_out_writes_one_event_and_a_repeat_writes_none(env: Env) -> None:
    user_id = await env.user()
    issued = await env.login(user_id)
    spare = await env.login(user_id)
    csrf = await csrf_of(env, issued.token)

    first = await sign_out(env, "/api/auth/logout", issued.token, csrf)
    again = await sign_out_again(env, "/api/auth/logout", issued.token)

    assert (first.status_code, again.status_code) == (204, 204)
    [event] = await events(env)
    assert (event.kind, event.user_id, event.ip) == ("sign_out", user_id, CLIENT_IP)
    assert event.detail == {"session_id": str(issued.id)}  # nothing else: no agent, no token
    still = await env.scalar("SELECT count(*) FROM sessions WHERE id = :s", s=spare.id)
    assert still == 1  # only this session ended


async def test_sign_out_everywhere_writes_one_event_with_the_count(env: Env) -> None:
    user_id = await env.user()
    other = await env.user("other@example.com")
    mine = [await env.login(user_id) for _ in range(3)]
    await env.login(other)
    csrf = await csrf_of(env, mine[0].token)

    first = await sign_out(env, "/api/auth/logout-all", mine[0].token, csrf)
    again = await sign_out_again(env, "/api/auth/logout-all", mine[0].token)

    assert (first.status_code, again.status_code) == (204, 401)  # the session is gone
    [event] = await events(env)
    assert (event.kind, event.user_id, event.ip) == ("sign_out_all", user_id, CLIENT_IP)
    assert event.detail == {"sessions": 3}
    assert await env.scalar("SELECT count(*) FROM sessions WHERE user_id = :u", u=other) == 1


async def test_a_session_already_ended_by_a_parallel_request_writes_no_sign_out(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The request resolved its session, then a parallel sign-out deleted it first."""
    user_id = await env.user()
    issued = await env.login(user_id)
    csrf = await csrf_of(env, issued.token)
    original = routes.revoke_session

    async def raced(db: AsyncSession, owner: UUID, session_id: UUID) -> bool:
        await env.run("DELETE FROM sessions WHERE id = :s", s=session_id)
        return await original(db, owner, session_id)

    monkeypatch.setattr(routes, "revoke_session", raced)
    response = await sign_out(env, "/api/auth/logout", issued.token, csrf)
    assert response.status_code == 204
    assert await events(env) == []


async def test_sign_out_everywhere_with_nothing_left_to_end_writes_no_event(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A reset committed between the session check and the lock: no session left to end."""
    user_id = await env.user()
    issued = await env.login(user_id)
    csrf = await csrf_of(env, issued.token)
    original = routes.revoke_user_sessions

    async def raced(db: AsyncSession, owner: UUID, **kwargs: Any) -> int:
        await env.run("DELETE FROM sessions WHERE user_id = :u", u=owner)
        return await original(db, owner, **kwargs)

    monkeypatch.setattr(routes, "revoke_user_sessions", raced)
    response = await sign_out(env, "/api/auth/logout-all", issued.token, csrf)
    assert response.status_code == 204
    assert await events(env) == []


# --- a failed insert never keeps the session -------------------------------------------------


@pytest.mark.parametrize("path", ["/api/auth/logout", "/api/auth/logout-all"])
async def test_a_failed_event_insert_still_signs_out(
    env: Env, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture, path: str
) -> None:
    """A real database error inside the transaction: without the savepoint the transaction is
    aborted and the commit of the revoke fails, leaving the session alive."""
    user_id = await env.user()
    issued = await env.login(user_id)
    csrf = await csrf_of(env, issued.token)

    async def failing(db: AsyncSession, kind: str, **kwargs: Any) -> None:
        assert kind in KINDS
        await db.execute(text("SELECT 1 / 0"))

    monkeypatch.setattr(routes, "record_event", failing)
    with caplog.at_level(logging.WARNING, logger="classbeat.auth.routes"):
        response = await sign_out(env, path, issued.token, csrf)

    assert response.status_code == 204
    assert "Max-Age=0" in response.headers["set-cookie"]
    assert await env.scalar("SELECT count(*) FROM sessions WHERE user_id = :u", u=user_id) == 0
    assert await events(env) == []
    [record] = [r for r in caplog.records if r.name == "classbeat.auth.routes"]
    assert record.getMessage() == (
        "sign-out event write failed: DataError (SQLSTATE 22012)"  # the class, never a value
    )


# --- the user's security events: export and deletion --------------------------------------------


async def test_sign_out_events_are_exported_and_deleted_with_the_account(env: Env) -> None:
    user_id = await env.user()
    first, second, third = [await env.login(user_id) for _ in range(3)]
    out = await sign_out(env, "/api/auth/logout", first.token, await csrf_of(env, first.token))
    assert out.status_code == 204
    out = await sign_out(env, "/api/auth/logout-all", third.token, await csrf_of(env, third.token))
    assert out.status_code == 204
    assert await env.scalar("SELECT count(*) FROM sessions WHERE id = :s", s=second.id) == 0

    async with env.db() as db:
        document = await account_export(db, user_id)
    exported = [e for e in document["security_events"] if e["kind"] in KINDS]
    assert [(e["kind"], e["detail"]) for e in exported] == [
        ("sign_out", {"session_id": str(first.id)}),
        ("sign_out_all", {"sessions": 2}),
    ]

    async with env.db() as db:
        assert await auth.delete_account(db, user_id)
    assert await events(env) == []


# --- no deadlock with an account deletion ------------------------------------------------------


async def test_sign_out_and_an_account_deletion_both_finish_without_a_deadlock(
    env: Env, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """The sign-out parks after its revoke (session row locked); the account deletion starts and
    must wait. Without the user row lock the deletion would hold the user row and wait for the
    session, and the event's foreign key would then wait for the user row: a deadlock, which
    fails the deletion or (as a failed write, logged) drops the sign-out's event."""
    user_id = await env.user()
    issued = await env.login(user_id)
    csrf = await csrf_of(env, issued.token)
    inside, release = parked(monkeypatch, routes, "revoke_session")

    async def delete_account() -> bool:
        async with env.db() as db:
            return await auth.delete_account(db, user_id)

    with caplog.at_level(logging.WARNING, logger="classbeat.auth.routes"):
        async with own_client(env) as client:
            signing_out = asyncio.ensure_future(
                client.client.post("/api/auth/logout", headers=unsafe_headers(issued.token, csrf))
            )
            await inside.wait()
            deleting = asyncio.ensure_future(delete_account())
            try:
                await until_it_waits(env, deleting)
            finally:
                release.set()
            response, deleted = await asyncio.gather(signing_out, deleting)

    assert response.status_code == 204
    assert deleted is True
    assert [r for r in caplog.records if r.name == "classbeat.auth.routes"] == []
    assert await env.scalar("SELECT count(*) FROM users") == 0
    assert await events(env) == []  # written, then removed with the account
