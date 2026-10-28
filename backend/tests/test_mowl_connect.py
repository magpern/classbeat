"""MOWL connect, status and disconnect through the API (ADR 0004 sections 4 to 6), against the
in-process fake MOWL (``mowlfake``) and the real ``mowl`` library. Never the network."""

import copy
import pickle
from typing import Any
from uuid import UUID

import pytest
from mowl import api as library_api
from mowl.credentials import Credentials as LibraryCredentials
from mowl.errors import MowlError
from sqlalchemy import text

from authtools import Env
from classbeat import connections
from classbeat.connections import ConnectionFailure
from classbeat.db import user_session
from ctx import make_context
from mowlfake import EMAIL, PASSWORD, TOKEN, FakeMowl

CONNECT = "/api/connections/mowl"
pytestmark = pytest.mark.usefixtures("mowl_api")


USER = "user@example.com"


async def actor_for(env: Env, make_actor: Any, email: str = USER) -> Any:
    return await make_actor(await env.user(email))


async def owned(env: Env, sql: str, email: str = USER) -> Any:
    """``sql`` as the user (RLS: the system session sees no connection rows)."""
    user_id = await env.scalar("SELECT id FROM users WHERE email = :e", e=email)
    assert isinstance(user_id, UUID)
    async with user_session(env.factory, make_context(user_id)) as db:
        return (await db.execute(text(sql))).scalar_one_or_none()


async def user_ctx(env: Env, email: str = USER) -> Any:
    user_id = await env.scalar("SELECT id FROM users WHERE email = :e", e=email)
    assert isinstance(user_id, UUID)
    return make_context(user_id)


def body(email: str = EMAIL, password: str = PASSWORD) -> dict[str, str]:
    return {"email": email, "password": password}


async def test_connect_stores_the_token_encrypted_and_shows_the_status(
    env: Env, make_actor: Any, mowl_api: FakeMowl
) -> None:
    actor = await actor_for(env, make_actor)
    response = await actor.post(CONNECT, body())
    assert response.status_code == 200, response.text
    shown = response.json()
    assert (shown["provider"], shown["status"], shown["last_error"]) == ("mowl", "connected", None)
    assert shown["expires_at"] is None  # the token's lifetime is unknown (ADR 0004 section 6)
    assert set(shown) == {
        "provider", "status", "last_error", "connected_at", "last_used_at", "expires_at"
    }  # fmt: skip
    assert mowl_api.requests == ["POST " + mowl_api_path("auth"), "GET " + mowl_api_path("ticket")]

    listed = (await actor.get("/api/connections")).json()["connections"]
    assert [(c["provider"], c["status"]) for c in listed] == [("mowl", "connected")]
    secret = await owned(env, "SELECT secret FROM connections")
    assert isinstance(secret, bytes) and TOKEN.encode() not in secret  # noqa: PT018
    ctx = await user_ctx(env)
    opened = await connections.open_connection(env.factory, env.app.state.keyring, ctx, "mowl")
    assert isinstance(opened.credentials, LibraryCredentials)
    assert opened.credentials.mowl_token == TOKEN


def mowl_api_path(which: str) -> str:
    from mowlfake import AUTHENTICATE, EXCHANGE

    return AUTHENTICATE if which == "auth" else EXCHANGE


async def test_a_wrong_password_is_invalid_credentials_and_stores_nothing(
    env: Env, make_actor: Any
) -> None:
    actor = await actor_for(env, make_actor)
    response = await actor.post(CONNECT, body(password="not-the-password"))
    assert response.status_code == 400
    assert response.json() == {"detail": {"reason": "invalid_credentials"}}
    assert await owned(env, "SELECT count(*) FROM connections") == 0


@pytest.mark.parametrize(
    ("where", "code", "status", "reason"),
    [
        ("authenticate", 401, 400, "invalid_credentials"),
        ("authenticate", 403, 400, "invalid_credentials"),
        ("authenticate", 503, 503, "upstream_unavailable"),
        ("exchange", 500, 503, "upstream_unavailable"),
        ("exchange", 418, 502, "unknown"),
    ],
)
async def test_mowl_errors_map_to_categories(
    env: Env, make_actor: Any, mowl_api: FakeMowl, where: str, code: int, status: int, reason: str
) -> None:
    mowl_api.status[where] = code
    actor = await actor_for(env, make_actor)
    response = await actor.post(CONNECT, body())
    assert (response.status_code, response.json()) == (status, {"detail": {"reason": reason}})
    assert await owned(env, "SELECT count(*) FROM connections") == 0


async def test_mowl_rate_limiting_passes_its_retry_after_hint(
    env: Env, make_actor: Any, mowl_api: FakeMowl
) -> None:
    mowl_api.status["authenticate"], mowl_api.retry_after = 429, "120"
    actor = await actor_for(env, make_actor)
    response = await actor.post(CONNECT, body())
    assert response.status_code == 429
    assert response.json() == {"detail": {"reason": "rate_limited"}}
    assert response.headers["Retry-After"] == "120"


async def test_an_unreachable_mowl_is_upstream_unavailable(
    env: Env, make_actor: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(library_api, "API", "http://127.0.0.1:9/")  # discard port: refused
    actor = await actor_for(env, make_actor)
    response = await actor.post(CONNECT, body())
    assert (response.status_code, response.json()["detail"]["reason"]) == (
        503,
        "upstream_unavailable",
    )


async def test_connect_and_disconnect_need_recent_auth(
    env: Env, make_actor: Any, mowl_api: FakeMowl
) -> None:
    actor = await actor_for(env, make_actor)
    assert (await actor.post(CONNECT, body())).status_code == 200
    await env.run("UPDATE sessions SET reauth_at = now() - interval '11 minutes'")
    again = await actor.post(CONNECT, body())
    assert (again.status_code, again.json()["detail"]["reason"]) == (403, "reauth_required")
    gone = await actor.call("DELETE", CONNECT)
    assert (gone.status_code, gone.json()["detail"]["reason"]) == (403, "reauth_required")
    assert len(mowl_api.requests) == 2  # only the first connect reached MOWL
    assert await owned(env, "SELECT count(*) FROM connections") == 1


async def test_a_user_with_totp_needs_a_step_up(
    env: Env, make_actor: Any, mowl_api: FakeMowl
) -> None:
    actor = await actor_for(env, make_actor)
    await actor.enroll()
    await env.run("UPDATE sessions SET stepup_at = NULL")
    response = await actor.post(CONNECT, body())
    assert (response.status_code, response.json()["detail"]["reason"]) == (403, "stepup_required")
    assert mowl_api.requests == []


async def test_anonymous_and_cross_site_requests_are_refused(env: Env, mowl_api: FakeMowl) -> None:
    assert (await env.client.get("/api/connections")).status_code == 401
    no_csrf = await env.client.post(CONNECT, json=body(), headers={"Origin": "https://evil.test"})
    assert no_csrf.status_code == 403
    assert mowl_api.requests == []


async def test_disconnect_deletes_the_token(env: Env, make_actor: Any) -> None:
    actor = await actor_for(env, make_actor)
    assert (await actor.post(CONNECT, body())).status_code == 200
    assert (await actor.call("DELETE", CONNECT)).status_code == 204
    assert await owned(env, "SELECT count(*) FROM connections") == 0
    assert (await actor.get("/api/connections")).json() == {"connections": []}
    assert (await actor.call("DELETE", CONNECT)).status_code == 204  # nothing left: still 204
    kinds = await env.scalar(
        "SELECT array_agg(kind ORDER BY at, id) FROM auth_events WHERE kind LIKE 'connection_%'"
    )
    assert kinds == ["connection_connected", "connection_disconnected"]


async def test_a_401_on_use_flips_to_needs_reconnect_and_reconnect_keeps_the_id(
    env: Env, make_actor: Any, mowl_api: FakeMowl
) -> None:
    actor = await actor_for(env, make_actor)
    assert (await actor.post(CONNECT, body())).status_code == 200
    ctx = await user_ctx(env)
    first_id = await owned(env, "SELECT id FROM connections")
    ring = env.app.state.keyring
    assert await connections.use(env.factory, ring, ctx, "mowl", list_ok) == []

    mowl_api.token = "ffffffff-ffff-ffff-ffff-ffffffffffff"  # MOWL forgot the stored token
    with pytest.raises(ConnectionFailure) as caught:
        await connections.use(env.factory, ring, ctx, "mowl", list_ok)
    assert caught.value.category == "needs_reconnect"
    shown = (await actor.get("/api/connections")).json()["connections"][0]
    assert (shown["status"], shown["last_error"]) == ("needs_reconnect", "needs_reconnect")
    with pytest.raises(ConnectionFailure) as stopped:  # no provider call while it needs one
        await connections.use(env.factory, ring, ctx, "mowl", list_ok)
    assert stopped.value.category == "not_connected"

    reconnected = await actor.post(CONNECT, body())
    assert (reconnected.status_code, reconnected.json()["status"]) == (200, "connected")
    assert await owned(env, "SELECT id FROM connections") == first_id
    assert await connections.use(env.factory, ring, ctx, "mowl", list_ok) == []


def list_ok(credentials: Any) -> Any:
    """A real library call through the user's credentials (``POST v1/programs``)."""
    return library_api.list_workouts(credentials)


async def test_library_errors_are_the_real_mowl_error() -> None:
    """The wrapper's default library error is the pinned library's own class."""
    outcome = connections.classify(MowlError("refused"), sign_in=True)
    assert outcome.category == "invalid_credentials"
    assert issubclass(connections.Credentials, LibraryCredentials)
    creds = connections.Credentials(mowl_token=TOKEN, swedebeat_device_id="device-1")
    assert repr(creds) == str(creds) == "Credentials(...)"  # the library's repr shows the device
    for duplicate in (copy.copy, copy.deepcopy, pickle.dumps):
        with pytest.raises(TypeError):
            duplicate(creds)


async def test_invalid_bodies_never_echo_the_password(env: Env, make_actor: Any) -> None:
    actor = await actor_for(env, make_actor)
    for sent in (
        {"email": "not an email", "password": PASSWORD},
        {"email": EMAIL, "password": PASSWORD * 100},
        {"password": PASSWORD},
        {"email": EMAIL, "password": ""},
    ):
        response = await actor.post(CONNECT, sent)
        assert response.status_code == 422
        assert PASSWORD not in response.text
        assert "input" not in response.text
