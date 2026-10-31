"""M5 security suite, follow-up (#227): admin request bodies FastAPI refuses before any
dependency, other than invalid JSON (``test_m5_security_admin_session.py``), from the #303 delta
review's notes 1 and 3.

- A body nested too deep to parse (``[`` x 50 000) is FastAPI's 400 "error parsing the body". On
  ``/api/admin`` the gate runs first: a non-admin gets the same 403 as with a valid body, an admin
  without step-up ``stepup_required`` with its row, and a stepped-up admin the 400 with one
  ``admin.request`` row refused ``body_unreadable``.
- A body over ``MAX_BODY_BYTES`` (64 KiB), by its ``Content-Length`` or streamed without one, is
  413 ``body_too_large`` behind the same gate, with one row refused ``body_too_large``. Exactly
  64 KiB is read (and here answered 422 for its content).

Threats covered: T90, T92, T93.
"""

import inspect
import json
import uuid
from collections.abc import AsyncIterator
from typing import Any

import fastapi.routing
import pytest
from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.engine import Connection, make_url

from authtools import ORIGIN, Env, running_env, session_cookie
from classbeat.admin.routes import (
    MAX_BODY_BYTES,
    _body_refusal,  # pyright: ignore[reportPrivateUsage]
)

ENTITLEMENTS = "/api/admin/users/{user_id}/entitlements"
CODES = "/api/admin/redeem-codes"
# Deeper than CPython's C recursion limit on every platform (5000 is enough on Windows, not on
# Linux, where it decodes to a plain ``json_invalid``), and under the body limit.
DEEP = b"[" * 50_000


@pytest.fixture
async def env(app_database_url: str, maint_database_url: str) -> AsyncIterator[Env]:
    url = make_url(maint_database_url)
    async with running_env(
        app_database_url,
        maint_database_url=url.set(password=None).render_as_string(hide_password=False),
        database_maint_password=url.password,
    ) as env:
        yield env


async def send_raw(actor: Any, path: str, content: Any, **headers: str) -> Any:
    info = await actor.client.get("/api/auth/session", headers=session_cookie(actor.token))
    response = await actor.client.post(
        path,
        headers={
            "Origin": ORIGIN,
            "X-CSRF-Token": info.json()["csrf_token"],
            "Content-Type": "application/json",
            **session_cookie(actor.token),
            **headers,
        },
        content=content,
    )
    actor.client.cookies.clear()
    return response


class Actors:
    def __init__(self, plain: Any, no_stepup: Any, stepped: Any, ids: dict[str, uuid.UUID]):
        self.plain, self.no_stepup, self.stepped, self.ids = plain, no_stepup, stepped, ids


async def actors(env: Env, make_actor: Any) -> Actors:
    ids = {
        "plain": await env.user("plain@example.com"),
        "no_stepup": await env.user("a@example.com", role="admin"),
        "stepped": await env.user("b@example.com", role="admin"),
    }
    made = {k: await make_actor(v, mfa=True) for k, v in ids.items()}
    await env.run(
        "UPDATE sessions SET stepup_at = now() WHERE user_id IN (:p, :s)",
        p=ids["plain"], s=ids["stepped"],
    )  # fmt: skip
    return Actors(made["plain"], made["no_stepup"], made["stepped"], ids)


def rows(conn: Connection, actor_id: object) -> list[tuple[str, str, str | None, Any]]:
    return [
        tuple(r)
        for r in conn.execute(
            text(
                "SELECT action, outcome, reason, params FROM admin_audit WHERE actor_id = :a "
                "ORDER BY created_at, id"
            ),
            {"a": actor_id},
        )
    ]


async def check_refused_behind_the_gate(
    env: Env, superuser: Connection, who: Actors, content: Any, status: int, reason: str,
    **headers: str,
) -> None:  # fmt: skip
    target = await env.user(f"t-{uuid.uuid4().hex[:8]}@example.com")
    cases = [(ENTITLEMENTS.replace("{user_id}", str(target)), ENTITLEMENTS), (CODES, CODES)]
    for path, template in cases:
        valid = await who.plain.call("POST", path, {"plan_id": "pro"})
        refused = await send_raw(who.plain, path, content() if callable(content) else content,
                                 **headers)  # fmt: skip
        assert (refused.status_code, refused.json()) == (valid.status_code, valid.json()), path
        assert refused.json() == {"detail": {"reason": "forbidden"}}
        early = await send_raw(who.no_stepup, path, content() if callable(content) else content,
                               **headers)  # fmt: skip
        assert (early.status_code, early.json()["detail"]["reason"]) == (403, "stepup_required")
        answered = await send_raw(who.stepped, path, content() if callable(content) else content,
                                  **headers)  # fmt: skip
        assert answered.status_code == status, (path, answered.text)
        assert answered.headers["cache-control"] == "no-store"
        assert rows(superuser, who.ids["stepped"])[-1] == (
            "admin.request", "refused", reason, {"method": "POST", "route": template}
        )  # fmt: skip
    assert rows(superuser, who.ids["plain"]) == []
    assert [r[1:3] for r in rows(superuser, who.ids["no_stepup"])] == [
        ("refused", "stepup_required")
    ] * 2
    assert [r[1:3] for r in rows(superuser, who.ids["stepped"])] == [("refused", reason)] * 2
    assert superuser.execute(text("SELECT count(*) FROM entitlements")).scalar_one() == 0
    assert superuser.execute(text("SELECT count(*) FROM redeem_codes")).scalar_one() == 0


async def test_a_body_nested_too_deep_to_parse_meets_the_gate_first(
    env: Env, make_actor: Any, superuser: Connection
) -> None:
    """Threats: T90, T92, T93."""
    assert len(DEEP) < MAX_BODY_BYTES
    with pytest.raises(RecursionError):  # what FastAPI's 400 comes from, on this platform too
        json.loads(DEEP)
    who = await actors(env, make_actor)
    await check_refused_behind_the_gate(env, superuser, who, DEEP, 400, "body_unreadable")


async def test_a_body_over_the_limit_by_its_content_length_is_413_behind_the_gate(
    env: Env, make_actor: Any, superuser: Connection
) -> None:
    """Threats: T90, T92, T93."""
    who = await actors(env, make_actor)
    big = b'{"plan_id": "pro", "note": "' + b"x" * MAX_BODY_BYTES + b'"}'
    await check_refused_behind_the_gate(env, superuser, who, big, 413, "body_too_large")


async def test_a_streamed_body_over_the_limit_is_413_without_a_content_length(
    env: Env, make_actor: Any, superuser: Connection
) -> None:
    """A chunked body has no ``Content-Length``: the bytes are counted as they come in.

    Threats: T90, T92, T93.
    """

    def streamed() -> AsyncIterator[bytes]:
        async def chunks() -> AsyncIterator[bytes]:
            yield b'{"plan_id": "pro", "note": "'
            for _ in range(5):
                yield b"x" * (MAX_BODY_BYTES // 4)
            yield b'"}'

        return chunks()

    who = await actors(env, make_actor)
    await check_refused_behind_the_gate(env, superuser, who, streamed, 413, "body_too_large")
    probe = await send_raw(who.stepped, CODES, streamed())  # what was sent had no length
    assert "content-length" not in {k.lower() for k in probe.request.headers}
    assert probe.status_code == 413


async def test_a_body_of_exactly_the_limit_is_read(
    env: Env, make_actor: Any, superuser: Connection
) -> None:
    """Threats: T90, T92."""
    who = await actors(env, make_actor)
    target = await env.user("t@example.com")
    head, tail = b'{"plan_id": "pro", "note": "', b'"}'
    exact = head + b"x" * (MAX_BODY_BYTES - len(head) - len(tail)) + tail
    assert len(exact) == MAX_BODY_BYTES
    response = await send_raw(who.stepped, ENTITLEMENTS.replace("{user_id}", str(target)), exact)
    assert response.status_code == 422, response.text  # read and validated: the note is too long
    assert [r[1:3] for r in rows(superuser, who.ids["stepped"])] == [("refused", "invalid_input")]
    over = await send_raw(who.stepped, ENTITLEMENTS.replace("{user_id}", str(target)), exact + b" ")
    assert (over.status_code, over.json()["detail"]["reason"]) == (413, "body_too_large")


def test_only_the_two_body_refusals_get_a_body_reason() -> None:
    """#315 review, non-blocking 1: a 400 or 413 an admin route raises itself is not labelled
    ``body_unreadable`` or ``body_too_large`` (nor audited a second time); and FastAPI still
    raises its body-parse failure with the detail this matches.

    Threats: T92.
    """
    assert _body_refusal(HTTPException(400, "There was an error parsing the body")) == (
        "body_unreadable"
    )
    assert _body_refusal(HTTPException(413, {"reason": "body_too_large"})) == "body_too_large"
    for other in (
        HTTPException(400, "bad request"),
        HTTPException(400, {"reason": "body_unreadable"}),
        HTTPException(413, "too large"),
        HTTPException(413, {"reason": "something_else"}),
        HTTPException(403, {"reason": "forbidden"}),
    ):
        assert _body_refusal(other) is None, other
    source = inspect.getsource(fastapi.routing)
    assert 'status_code=400, detail="There was an error parsing the body"' in source


async def test_the_limit_is_the_admin_areas_alone() -> None:
    """``AdminBodyLimit`` itself raises only under ``/api/admin``: outside it, a large body is not
    its business (the app-wide ``BodyLimit`` of #351 refuses it before the app runs, see
    ``test_m6_security_body_limit``).

    Threats: T90.
    """
    from classbeat.admin.routes import AdminBodyLimit

    async def app(scope: Any, receive: Any, send: Any) -> None:
        message = await receive()  # would raise the 413 for an admin path
        assert len(message["body"]) == MAX_BODY_BYTES + 10

    async def receive() -> dict[str, Any]:
        return {"type": "http.request", "body": b"x" * (MAX_BODY_BYTES + 10), "more_body": False}

    async def send(message: Any) -> None:
        raise AssertionError(message)

    scope = {"type": "http", "path": "/api/account/redeem", "headers": []}
    await AdminBodyLimit(app)(scope, receive, send)
    with pytest.raises(HTTPException) as refused:
        await AdminBodyLimit(app)({**scope, "path": "/api/admin/codes"}, receive, send)
    assert refused.value.status_code == 413
