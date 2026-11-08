"""The user's own security activity and signing out one session (#678, ADR 0003 amendment of
2026-11-07).

``GET /api/auth/events`` lists the caller's own ``auth_events``: only their rows, only the kinds of
``activity.SHOWN_KINDS``, only the listed fields, newest first in keyset pages.
``DELETE /api/auth/sessions/{id}`` writes one ``sign_out`` when it ended a session, under the user
row lock (ADR 0019 section 6), in a savepoint (``routes._record_sign_out``).
"""

import asyncio
import json
import logging
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from authtools import Env, session_cookie, unsafe_headers
from classbeat import auth
from classbeat.auth import activity, routes
from test_auth_reset_races import against_a_held_lock, own_client, parked, until_it_waits

pytestmark = pytest.mark.usefixtures("frozen_rate_windows")

CLIENT_IP = "127.0.0.1"  # httpx's ASGITransport peer
FIELDS = {"id", "kind", "at", "ip", "provider", "other_session", "passkey"}
T0 = datetime(2026, 11, 1, 12, 0, tzinfo=UTC)


async def add_event(
    env: Env,
    user_id: UUID | None,
    kind: str,
    at: datetime,
    *,
    ip: str | None = "198.51.100.7",
    detail: dict[str, Any] | None = None,
) -> UUID:
    event_id = uuid4()
    async with env.db() as db:
        await db.execute(
            text(
                "INSERT INTO auth_events (id, user_id, kind, ip, at, detail) "
                "VALUES (:id, :u, :k, :ip, :at, CAST(:d AS jsonb))"
            ),
            {
                "id": event_id,
                "u": user_id,
                "k": kind,
                "ip": ip,
                "at": at,
                "d": None if detail is None else json.dumps(detail),
            },
        )
    return event_id


async def listing(env: Env, token: str, query: str = "") -> httpx.Response:
    return await env.client.get(f"/api/auth/events{query}", headers=session_cookie(token))


async def csrf_of(env: Env, token: str) -> str:
    response = await env.client.get("/api/auth/session", headers=session_cookie(token))
    return response.json()["csrf_token"]


async def sign_outs(env: Env) -> list[Any]:
    async with env.db() as db:
        result = await db.execute(
            text("SELECT user_id, ip, detail FROM auth_events WHERE kind = 'sign_out' ORDER BY at")
        )
        return list(result.all())


async def delete_session(env: Env, token: str, target: UUID | str) -> httpx.Response:
    csrf = await csrf_of(env, token)
    return await env.client.delete(
        f"/api/auth/sessions/{target}", headers=unsafe_headers(token, csrf)
    )


# --- the list: isolation, kinds and fields ------------------------------------------------------


async def test_a_user_sees_only_their_own_events(env: Env) -> None:
    alice = await env.user("alice@example.com")
    bob = await env.user("bob@example.com")
    mine = await add_event(env, alice, "sign_in", T0, ip="192.0.2.1")
    theirs = await add_event(env, bob, "sign_in", T0 + timedelta(minutes=1), ip="192.0.2.2")
    await add_event(env, None, "sign_in_failed", T0 + timedelta(minutes=2), ip="192.0.2.3")
    alice_session = await env.login(alice)
    bob_session = await env.login(bob)

    seen_by_alice = await listing(env, alice_session.token)
    seen_by_bob = await listing(env, bob_session.token)

    assert seen_by_alice.status_code == 200
    assert [e["id"] for e in seen_by_alice.json()["events"]] == [str(mine)]
    assert [e["id"] for e in seen_by_bob.json()["events"]] == [str(theirs)]
    for leaked in (str(theirs), "192.0.2.2", "192.0.2.3"):
        assert leaked not in seen_by_alice.text
    # Bob's newest event as a cursor gives Alice only her own older rows, never his.
    cursor = activity.encode_cursor(T0 + timedelta(minutes=5), uuid4())
    crossed = await listing(env, alice_session.token, f"?before={cursor}")
    assert [e["id"] for e in crossed.json()["events"]] == [str(mine)]


async def test_an_anonymous_caller_gets_401(env: Env) -> None:
    response = await env.client.get("/api/auth/events")
    assert response.status_code == 401


async def test_only_the_shown_kinds_and_fields_are_listed(env: Env) -> None:
    user_id = await env.user()
    issued = await env.login(user_id)
    hidden = ("connections_wiped", "admin_granted", "connection_connected", "some_future_kind")
    for offset, kind in enumerate(hidden):
        await add_event(env, user_id, kind, T0 + timedelta(seconds=offset), detail={"x": "y"})
    await add_event(
        env,
        user_id,
        "sign_in",
        T0 + timedelta(minutes=1),
        detail={"provider": "google", "secret": "never-shown"},
    )
    await add_event(
        env, user_id, "reauth_failed", T0 + timedelta(minutes=2), detail={"provider": "mowl"}
    )
    await add_event(
        env,
        user_id,
        "sign_out",
        T0 + timedelta(minutes=3),
        detail={"session_id": str(uuid4()), "from_session": str(issued.id)},
    )
    await add_event(env, user_id, "password_changed", T0 + timedelta(minutes=4), ip=None)

    body = (await listing(env, issued.token)).json()

    assert [(e["kind"], e["provider"], e["other_session"], e["ip"]) for e in body["events"]] == [
        ("password_changed", None, False, None),
        ("sign_out", None, True, "198.51.100.7"),
        ("reauth_failed", None, False, "198.51.100.7"),  # not a sign-in provider: dropped
        ("sign_in", "google", False, "198.51.100.7"),
    ]
    assert all(set(e) == FIELDS for e in body["events"])
    text_of_page = str(body)
    assert "never-shown" not in text_of_page
    assert str(issued.id) not in text_of_page  # the session ids in detail stay inside
    assert body["next_before"] is None


async def test_passkey_kinds_and_the_passkey_method_are_listed_without_their_detail(
    env: Env,
) -> None:
    """ADR 0019 section 10: the passkey kinds are shown, and a sign-in, re-authentication or
    failure with ``method: passkey`` is flagged; the passkey's row id in ``detail`` never is."""
    user_id = await env.user()
    issued = await env.login(user_id)
    row_id = str(uuid4())
    shown = (
        ("passkey_added", {"passkey": row_id}),
        ("passkey_renamed", {"passkey": row_id}),
        ("passkey_removed", {"passkey": row_id}),
        ("passkey_counter_regressed", {"method": "passkey", "reason": "counter"}),
        ("sign_in", {"method": "passkey", "passkey": row_id}),
        ("reauth", {"method": "passkey", "passkey": row_id}),
        ("sign_in_failed", {"method": "passkey", "reason": "signature"}),
        ("reauth_failed", {"method": "passkey", "reason": "uv"}),
        ("sign_in", {"method": "password"}),
    )
    for offset, (kind, detail) in enumerate(shown):
        await add_event(env, user_id, kind, T0 + timedelta(seconds=offset), detail=detail)

    body = (await listing(env, issued.token)).json()

    assert [(e["kind"], e["passkey"]) for e in reversed(body["events"])] == [
        ("passkey_added", False),
        ("passkey_renamed", False),
        ("passkey_removed", False),
        ("passkey_counter_regressed", True),
        ("sign_in", True),
        ("reauth", True),
        ("sign_in_failed", True),
        ("reauth_failed", True),
        ("sign_in", False),
    ]
    assert all(set(e) == FIELDS for e in body["events"])
    assert row_id not in str(body)


def test_the_shown_kinds_are_kinds_the_code_writes() -> None:
    """Each shown kind is a literal the app writes somewhere, so none is a typo."""
    from pathlib import Path

    source = "\n".join(
        path.read_text(encoding="utf-8")
        for path in Path(auth.__file__).parent.parent.rglob("*.py")
        if path.name != "activity.py"
    )
    missing = {kind for kind in activity.SHOWN_KINDS if f'"{kind}"' not in source}
    assert missing == set()


def test_the_frontend_fixture_lists_the_shown_kinds() -> None:
    """``fixtures/activity_kinds.json`` is ``SHOWN_KINDS`` written out; the frontend test holds
    its own list (``ACTIVITY_KINDS``, each worded in every catalogue) equal to the same file."""
    from pathlib import Path

    fixture = Path(__file__).parent / "fixtures" / "activity_kinds.json"
    assert json.loads(fixture.read_text(encoding="utf-8")) == sorted(activity.SHOWN_KINDS)


# --- paging ----------------------------------------------------------------------------------


async def test_pages_follow_the_cursor_without_skipping_equal_times(env: Env) -> None:
    user_id = await env.user()
    issued = await env.login(user_id)
    same_time = [await add_event(env, user_id, "sign_in", T0) for _ in range(3)]
    older = await add_event(env, user_id, "sign_out", T0 - timedelta(microseconds=1))
    newer = await add_event(env, user_id, "reauth", T0 + timedelta(microseconds=1))
    expected = [str(newer), *sorted((str(e) for e in same_time), reverse=True), str(older)]

    seen: list[str] = []
    query = "?limit=2"
    pages = 0
    while True:
        body = (await listing(env, issued.token, query)).json()
        pages += 1
        seen += [e["id"] for e in body["events"]]
        if body["next_before"] is None:
            break
        query = f"?limit=2&before={body['next_before']}"
    assert seen == expected
    assert pages == 3


async def test_the_default_page_is_twenty_and_the_limit_is_checked(env: Env) -> None:
    user_id = await env.user()
    issued = await env.login(user_id)
    for n in range(activity.PAGE_DEFAULT + 1):
        await add_event(env, user_id, "sign_in", T0 + timedelta(seconds=n))

    first = (await listing(env, issued.token)).json()
    assert len(first["events"]) == activity.PAGE_DEFAULT
    assert first["next_before"] is not None
    for bad in ("0", "101", "x"):
        response = await listing(env, issued.token, f"?limit={bad}")
        assert response.status_code == 422


@pytest.mark.parametrize(
    "cursor",
    [
        "1_" + "0" * 31,  # short id
        "x_" + "0" * 32,
        "1-" + "0" * 32,
        "9999999999999999999_" + "0" * 32,  # past datetime's range
        "1_" + "0" * 32 + "' OR 1=1",
    ],
)
async def test_a_cursor_of_another_shape_is_refused(env: Env, cursor: str) -> None:
    user_id = await env.user()
    issued = await env.login(user_id)
    response = await env.client.get(
        "/api/auth/events", params={"before": cursor}, headers=session_cookie(issued.token)
    )
    assert response.status_code == 422


def test_the_cursor_round_trips_to_the_microsecond() -> None:
    at = datetime(2026, 11, 7, 8, 9, 10, 123456, tzinfo=UTC)
    event_id = uuid4()
    assert activity.decode_cursor(activity.encode_cursor(at, event_id)) == (at, event_id)


async def test_the_library_refuses_a_limit_out_of_range(env: Env) -> None:
    async with env.db() as db:
        for bad in (0, activity.PAGE_MAX + 1):
            with pytest.raises(ValueError, match="limit"):
                await activity.list_activity(db, uuid4(), limit=bad, before=None)


# --- signing out one session writes a sign_out -----------------------------------------------


async def test_signing_out_another_session_writes_one_event(env: Env) -> None:
    user_id = await env.user()
    here = await env.login(user_id)
    there = await env.login(user_id)

    first = await delete_session(env, here.token, there.id)
    again = await delete_session(env, here.token, there.id)

    assert (first.status_code, again.status_code) == (204, 404)
    [event] = await sign_outs(env)
    assert (event.user_id, event.ip) == (user_id, CLIENT_IP)
    assert event.detail == {"session_id": str(there.id), "from_session": str(here.id)}
    [shown] = [e for e in (await listing(env, here.token)).json()["events"]]
    assert (shown["kind"], shown["other_session"]) == ("sign_out", True)


async def test_signing_out_this_session_from_the_list_is_a_plain_sign_out(env: Env) -> None:
    user_id = await env.user()
    here = await env.login(user_id)
    response = await delete_session(env, here.token, here.id)
    assert response.status_code == 204
    assert "Max-Age=0" in response.headers["set-cookie"]
    [event] = await sign_outs(env)
    assert event.detail == {"session_id": str(here.id)}


async def test_another_users_session_is_404_and_writes_nothing(env: Env) -> None:
    alice = await env.user("alice@example.com")
    bob = await env.user("bob@example.com")
    mine = await env.login(alice)
    theirs = await env.login(bob)

    response = await delete_session(env, mine.token, theirs.id)
    unknown = await delete_session(env, mine.token, uuid4())

    assert (response.status_code, unknown.status_code) == (404, 404)
    assert response.json() == unknown.json()
    assert await sign_outs(env) == []
    assert await env.scalar("SELECT count(*) FROM sessions WHERE id = :s", s=theirs.id) == 1


async def test_a_session_already_ended_by_a_parallel_request_writes_none(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    user_id = await env.user()
    here = await env.login(user_id)
    there = await env.login(user_id)
    csrf = await csrf_of(env, here.token)
    original = routes.revoke_session

    async def raced(db: AsyncSession, owner: UUID, session_id: UUID) -> bool:
        await env.run("DELETE FROM sessions WHERE id = :s", s=session_id)
        return await original(db, owner, session_id)

    monkeypatch.setattr(routes, "revoke_session", raced)
    response = await env.client.delete(
        f"/api/auth/sessions/{there.id}", headers=unsafe_headers(here.token, csrf)
    )
    assert response.status_code == 404
    assert await sign_outs(env) == []


# --- a failed insert never keeps the session: both branches of _record_sign_out --------------


async def test_a_database_error_in_the_event_still_ends_the_session(
    env: Env, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    user_id = await env.user()
    here = await env.login(user_id)
    there = await env.login(user_id)
    csrf = await csrf_of(env, here.token)

    async def failing(db: AsyncSession, kind: str, **kwargs: Any) -> None:
        assert kind == "sign_out"
        await db.execute(text("SELECT 1 / 0"))

    monkeypatch.setattr(routes, "record_event", failing)
    with caplog.at_level(logging.WARNING, logger="classbeat.auth.routes"):
        response = await env.client.delete(
            f"/api/auth/sessions/{there.id}", headers=unsafe_headers(here.token, csrf)
        )

    assert response.status_code == 204
    assert await env.scalar("SELECT count(*) FROM sessions WHERE id = :s", s=there.id) == 0
    assert await sign_outs(env) == []
    [record] = [r for r in caplog.records if r.name == "classbeat.auth.routes"]
    assert record.getMessage() == "sign-out event write failed: DataError (SQLSTATE 22012)"


@pytest.mark.parametrize("path", ["/api/auth/logout", "/api/auth/logout-all", "other"])
async def test_any_other_error_in_the_event_is_logged_by_class_and_the_sign_out_commits(
    env: Env, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture, path: str
) -> None:
    """The catch-all branch: an exception that is not a driver error (here after a real write in
    the savepoint, which must roll back) is logged by class only, never by its message."""
    user_id = await env.user()
    here = await env.login(user_id)
    there = await env.login(user_id)
    csrf = await csrf_of(env, here.token)

    async def failing(db: AsyncSession, kind: str, **kwargs: Any) -> None:
        await auth.record_event(db, kind, **kwargs)  # rolled back with the savepoint
        raise RuntimeError("value-that-must-not-be-logged")

    monkeypatch.setattr(routes, "record_event", failing)
    with caplog.at_level(logging.WARNING, logger="classbeat.auth.routes"):
        if path == "other":
            response = await env.client.delete(
                f"/api/auth/sessions/{there.id}", headers=unsafe_headers(here.token, csrf)
            )
            ended = there.id
        else:
            response = await env.client.post(path, headers=unsafe_headers(here.token, csrf))
            ended = here.id

    assert response.status_code == 204
    assert await env.scalar("SELECT count(*) FROM sessions WHERE id = :s", s=ended) == 0
    assert await env.scalar("SELECT count(*) FROM auth_events WHERE user_id = :u", u=user_id) == 0
    [record] = [r for r in caplog.records if r.name == "classbeat.auth.routes"]
    assert record.getMessage() == "sign-out event write failed: RuntimeError"
    assert "value-that-must-not-be-logged" not in caplog.text


# --- the user row lock comes first --------------------------------------------------------------


async def test_signing_out_one_session_waits_for_the_user_row_lock(env: Env) -> None:
    """With the user row held (``FOR NO KEY UPDATE``, as a reset or an account deletion holds
    it) the delete waits; neither its session delete nor the event's foreign key would."""
    user_id = await env.user()
    here = await env.login(user_id)
    there = await env.login(user_id)
    csrf = await csrf_of(env, here.token)
    async with own_client(env) as client:
        response = await against_a_held_lock(
            env,
            user_id,
            client.client.delete(
                f"/api/auth/sessions/{there.id}", headers=unsafe_headers(here.token, csrf)
            ),
        )
    assert response.status_code == 204
    [event] = await sign_outs(env)
    assert event.detail["session_id"] == str(there.id)


async def test_signing_out_one_session_and_an_account_deletion_do_not_deadlock(
    env: Env, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """The delete parks after its revoke (session row locked); the account deletion starts and
    must wait for it, then both finish and the event goes with the account."""
    user_id = await env.user()
    here = await env.login(user_id)
    there = await env.login(user_id)
    csrf = await csrf_of(env, here.token)
    inside, release = parked(monkeypatch, routes, "revoke_session")

    async def delete_account() -> bool:
        async with env.db() as db:
            return await auth.delete_account(db, user_id)

    with caplog.at_level(logging.WARNING, logger="classbeat.auth.routes"):
        async with own_client(env) as client:
            signing_out = asyncio.ensure_future(
                client.client.delete(
                    f"/api/auth/sessions/{there.id}", headers=unsafe_headers(here.token, csrf)
                )
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
    assert await sign_outs(env) == []  # written, then removed with the account
