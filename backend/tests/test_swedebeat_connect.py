"""Swedebeat connect, status and disconnect through the API (ADR 0015), against the in-process
fake Swedebeat (``swedebeatcore``: it decodes the library's AES-encoded fields with a TEST key made
for the run) and the real ``mowl.swedebeat`` library. Never the network."""

import json
from collections.abc import AsyncIterator
from typing import Any
from uuid import UUID

import pytest
from mowl import swedebeat as library_swedebeat
from pydantic import SecretStr
from sqlalchemy import Connection, text

from authtools import ORIGIN, Env, running_env
from classbeat import connections
from classbeat.connections import ConnectionFailure
from classbeat.connections.swedebeat_account import device_id_for
from classbeat.db import user_session
from ctx import make_context
from swedebeatcore import FakeSwedebeat
from swedebeatfake import EMAIL, EMAIL_B, PASSWORD, PASSWORD_B

CONNECT = "/api/connections/swedebeat"
pytestmark = pytest.mark.usefixtures("swedebeat_api")
USER = "user@example.com"


@pytest.fixture
async def env(
    clean_auth: None, app_database_url: str, swedebeat_app: dict[str, str]
) -> AsyncIterator[Env]:
    """The app with Swedebeat switched on and the test app key mounted as the secret file."""
    async with running_env(
        app_database_url, swedebeat_enabled=True, swedebeat_app=SecretStr(json.dumps(swedebeat_app))
    ) as running:
        yield running


async def actor_for(env: Env, make_actor: Any, email: str = USER) -> Any:
    return await make_actor(await env.user(email))


async def user_ctx(env: Env, email: str = USER) -> Any:
    user_id = await env.scalar("SELECT id FROM users WHERE email = :e", e=email)
    assert isinstance(user_id, UUID)
    return make_context(user_id)


async def owned(env: Env, sql: str, email: str = USER) -> Any:
    async with user_session(env.factory, await user_ctx(env, email)) as db:
        return (await db.execute(text(sql))).scalar_one_or_none()


def body(email: str = EMAIL, password: str = PASSWORD, **more: Any) -> dict[str, Any]:
    return {"email": email, "password": password, **more}


async def opened(env: Env, email: str = USER) -> Any:
    return await connections.open_connection(
        env.factory, env.app.state.keyring, await user_ctx(env, email), "swedebeat"
    )


def catalogue_search(credentials: Any) -> Any:
    """A real library call that carries the user's token (``GET track/extendedsearch``)."""
    return library_swedebeat.match_track(credentials, "Some Song", "Some Artist")


async def test_connect_stores_token_and_device_id_encrypted_and_shows_the_status(
    env: Env, make_actor: Any, swedebeat_api: FakeSwedebeat
) -> None:
    actor = await actor_for(env, make_actor)
    response = await actor.post(CONNECT, body())
    assert response.status_code == 200, response.text
    shown = response.json()
    assert (shown["provider"], shown["status"], shown["last_error"]) == (
        "swedebeat", "connected", None,
    )  # fmt: skip
    assert shown["account"] == EMAIL
    # The fake decoded the app's AES fields with the test key: the app encodes correctly.
    [login] = swedebeat_api.logins
    assert login == {
        "email": EMAIL,
        "device_id": device_id_for(secret_of(env), await user_id(env)),
        "force": False,
        "ok": True,
    }  # fmt: skip
    listed = (await actor.get("/api/connections")).json()
    assert [(c["provider"], c["status"], c["account"]) for c in listed["connections"]] == [
        ("swedebeat", "connected", EMAIL)
    ]
    assert listed["swedebeat"] == {"enabled": True, "available": True}
    secret = await owned(env, "SELECT secret FROM connections")
    [token] = swedebeat_api.tokens
    assert isinstance(secret, bytes) and token.encode() not in secret  # noqa: PT018
    assert login["device_id"].encode() not in secret
    ctx = await user_ctx(env)
    row = await connections.open_connection(env.factory, env.app.state.keyring, ctx, "swedebeat")
    assert row.credentials.swedebeat_token == token
    assert row.credentials.swedebeat_device_id == login["device_id"]
    # Nothing but the email is kept in the clear, and never the password.
    public = await owned(env, "SELECT public::text FROM connections")
    assert json.loads(public) == {"email": EMAIL}
    assert PASSWORD not in response.text


def secret_of(env: Env) -> bytes:
    key = env.settings.app_secret_key
    assert key is not None
    return key.get_secret_value().encode()


async def user_id(env: Env, email: str = USER) -> UUID:
    found = await env.scalar("SELECT id FROM users WHERE email = :e", e=email)
    assert isinstance(found, UUID)
    return found


async def test_a_wrong_password_is_invalid_credentials_and_stores_nothing(
    env: Env, make_actor: Any
) -> None:
    actor = await actor_for(env, make_actor)
    response = await actor.post(CONNECT, body(password="not-the-password"))
    assert (response.status_code, response.json()) == (
        400, {"detail": {"reason": "invalid_credentials"}},
    )  # fmt: skip
    assert await owned(env, "SELECT count(*) FROM connections") == 0
    unknown = await actor.post(CONNECT, body(email="nobody@example.test"))
    assert (unknown.status_code, unknown.json()["detail"]["reason"]) == (400, "invalid_credentials")


async def test_a_device_in_use_is_409_until_the_user_confirms_and_force_takes_the_session(
    env: Env, make_actor: Any, swedebeat_api: FakeSwedebeat
) -> None:
    swedebeat_api.hold(EMAIL, "phone-device-0001")
    actor = await actor_for(env, make_actor)
    refused = await actor.post(CONNECT, body())
    assert (refused.status_code, refused.json()) == (
        409, {"detail": {"reason": "device_in_use"}},
    )  # fmt: skip
    assert await owned(env, "SELECT count(*) FROM connections") == 0
    assert swedebeat_api.accounts[EMAIL].holder == "phone-device-0001"  # the phone was not kicked
    forced = await actor.post(CONNECT, body(force=True))
    assert forced.status_code == 200, forced.text
    assert [(entry["force"], entry["ok"]) for entry in swedebeat_api.logins] == [
        (False, False), (True, True),
    ]  # fmt: skip
    assert swedebeat_api.accounts[EMAIL].holder == swedebeat_api.logins[-1]["device_id"]


async def test_another_wording_of_a_taken_session_is_still_device_in_use(
    env: Env, make_actor: Any, swedebeat_api: FakeSwedebeat
) -> None:
    swedebeat_api.hold(EMAIL, "phone-device-0001")
    swedebeat_api.in_use_message = "You are already logged in on another device"
    actor = await actor_for(env, make_actor)
    response = await actor.post(CONNECT, body())
    assert (response.status_code, response.json()["detail"]["reason"]) == (409, "device_in_use")


@pytest.mark.parametrize("message", ["Session expired", "Something else went wrong", ""])
async def test_any_other_refusal_is_invalid_credentials_never_a_force(
    env: Env, make_actor: Any, swedebeat_api: FakeSwedebeat, message: str
) -> None:
    swedebeat_api.hold(EMAIL, "phone-device-0001")
    swedebeat_api.in_use_message = message
    actor = await actor_for(env, make_actor)
    response = await actor.post(CONNECT, body())
    assert (response.status_code, response.json()["detail"]["reason"]) == (
        400,
        "invalid_credentials",
    )


async def test_an_outage_is_upstream_unavailable(
    env: Env, make_actor: Any, swedebeat_api: FakeSwedebeat
) -> None:
    swedebeat_api.outage = True
    actor = await actor_for(env, make_actor)
    response = await actor.post(CONNECT, body())
    assert (response.status_code, response.json()) == (
        503, {"detail": {"reason": "upstream_unavailable"}},
    )  # fmt: skip
    assert await owned(env, "SELECT count(*) FROM connections") == 0


async def test_an_unreachable_swedebeat_is_upstream_unavailable(
    env: Env, make_actor: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(library_swedebeat, "WWW", "http://127.0.0.1:9/")  # discard port: refused
    actor = await actor_for(env, make_actor)
    response = await actor.post(CONNECT, body())
    assert (response.status_code, response.json()["detail"]["reason"]) == (
        503, "upstream_unavailable",
    )  # fmt: skip


async def test_a_wrong_app_key_is_not_a_wrong_password(
    env: Env, make_actor: Any, swedebeat_api: FakeSwedebeat
) -> None:
    """A key the fake cannot decode with is a malformed request to Swedebeat, not a bad login."""
    other = {"key": "k" * 32, "iv": "i" * 16}
    env.app.state.settings = env.settings.model_copy(
        update={"swedebeat_app": SecretStr(json.dumps(other))}
    )
    actor = await actor_for(env, make_actor)
    response = await actor.post(CONNECT, body())
    assert (response.status_code, response.json()["detail"]["reason"]) == (502, "unknown")
    assert swedebeat_api.logins == []


async def test_without_the_app_key_connect_says_so_and_sends_nothing(
    env: Env, make_actor: Any, swedebeat_api: FakeSwedebeat, tmp_path: Any
) -> None:
    env.app.state.settings = env.settings.model_copy(
        update={"swedebeat_app": None, "swedebeat_app_file": tmp_path / "missing.json"}
    )
    actor = await actor_for(env, make_actor)
    response = await actor.post(CONNECT, body())
    assert (response.status_code, response.json()) == (
        503, {"detail": {"reason": "app_key_unavailable"}},
    )  # fmt: skip
    assert swedebeat_api.logins == []
    assert (await actor.get("/api/connections")).json()["swedebeat"] == {
        "enabled": True, "available": False,
    }  # fmt: skip
    # No rate-limit slot was spent on a request that sent nothing: 10 more refusals, no 429.
    for _ in range(10):
        assert (await actor.post(CONNECT, body())).status_code == 503


async def test_the_image_file_is_the_key_when_no_secret_is_mounted(
    env: Env,
    make_actor: Any,
    swedebeat_api: FakeSwedebeat,
    swedebeat_app: dict[str, str],
    tmp_path: Any,
) -> None:
    baked = tmp_path / "swedebeat_app.json"
    baked.write_text(json.dumps(swedebeat_app))
    env.app.state.settings = env.settings.model_copy(
        update={"swedebeat_app": None, "swedebeat_app_file": baked}
    )
    actor = await actor_for(env, make_actor)
    assert (await actor.post(CONNECT, body())).status_code == 200
    assert [entry["ok"] for entry in swedebeat_api.logins] == [True]


def store_flag(conn: Connection, value: str) -> None:
    conn.execute(
        text(
            "INSERT INTO app_settings (key, value) VALUES ('swedebeat_enabled', CAST(:v AS jsonb)) "
            "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value"
        ),
        {"v": json.dumps(value)},
    )
    conn.commit()


@pytest.fixture(autouse=True)
def clean_flag(superuser: Connection) -> Any:
    superuser.execute(text("DELETE FROM app_settings"))
    superuser.commit()
    yield
    superuser.execute(text("DELETE FROM app_settings"))
    superuser.commit()


async def test_while_the_flag_is_off_connect_is_404_and_disconnect_still_works(
    env: Env, make_actor: Any, swedebeat_api: FakeSwedebeat, superuser: Connection
) -> None:
    actor = await actor_for(env, make_actor)
    assert (await actor.post(CONNECT, body())).status_code == 200
    store_flag(superuser, "off")
    refused = await actor.post(CONNECT, body())
    assert (refused.status_code, refused.json()) == (404, {"detail": {"reason": "feature_off"}})
    assert len(swedebeat_api.logins) == 1  # nothing reached Swedebeat the second time
    assert (await actor.get("/api/connections")).json()["swedebeat"]["enabled"] is False
    assert (await actor.call("DELETE", CONNECT)).status_code == 204  # always allowed
    assert await owned(env, "SELECT count(*) FROM connections") == 0


async def test_the_environment_switch_off_is_404_whatever_the_admin_flag_says(
    env: Env, make_actor: Any, superuser: Connection
) -> None:
    env.app.state.settings = env.settings.model_copy(update={"swedebeat_enabled": False})
    store_flag(superuser, "on")
    actor = await actor_for(env, make_actor)
    response = await actor.post(CONNECT, body())
    assert (response.status_code, response.json()["detail"]["reason"]) == (404, "feature_off")


async def test_the_device_id_is_one_per_user_and_stable_across_reconnect_and_disconnect(
    env: Env, make_actor: Any, swedebeat_api: FakeSwedebeat
) -> None:
    actor = await actor_for(env, make_actor)
    other = await actor_for(env, make_actor, "other@example.com")
    secret = secret_of(env)
    assert (await actor.post(CONNECT, body())).status_code == 200
    first = (await opened(env)).credentials.swedebeat_device_id
    assert first == device_id_for(secret, await user_id(env)) and len(first) == 16  # noqa: PT018
    assert (await actor.post(CONNECT, body())).status_code == 200  # a reconnect
    assert (await opened(env)).credentials.swedebeat_device_id == first
    assert (await actor.call("DELETE", CONNECT)).status_code == 204
    assert (await actor.post(CONNECT, body())).status_code == 200  # after a disconnect
    assert (await opened(env)).credentials.swedebeat_device_id == first
    assert (await other.post(CONNECT, body(EMAIL_B, PASSWORD_B))).status_code == 200
    second = (await opened(env, "other@example.com")).credentials.swedebeat_device_id
    assert second == device_id_for(secret, await user_id(env, "other@example.com"))
    assert second != first
    # A reconnect is the same device, so it never meets "device in use" with force off.
    assert {entry["device_id"] for entry in swedebeat_api.logins if entry["email"] == EMAIL} == {
        first
    }


async def test_two_users_never_mix_tokens_device_ids_or_calls(
    env: Env, make_actor: Any, swedebeat_api: FakeSwedebeat
) -> None:
    alice = await actor_for(env, make_actor, "alice@example.com")
    bob = await actor_for(env, make_actor, "bob@example.com")
    assert (await alice.post(CONNECT, body())).status_code == 200
    assert (await bob.post(CONNECT, body(EMAIL_B, PASSWORD_B))).status_code == 200
    a, b = await opened(env, "alice@example.com"), await opened(env, "bob@example.com")
    assert a.credentials.swedebeat_token != b.credentials.swedebeat_token
    assert a.credentials.swedebeat_device_id != b.credentials.swedebeat_device_id
    ring = env.app.state.keyring
    for who in ("alice@example.com", "bob@example.com", "alice@example.com"):
        await connections.use(
            env.factory, ring, await user_ctx(env, who), "swedebeat", catalogue_search
        )
    # The fake recorded which account's token each call carried.
    assert [call["email"] for call in swedebeat_api.calls] == [EMAIL, EMAIL_B, EMAIL]
    assert (await alice.get("/api/connections")).json()["connections"][0]["account"] == EMAIL
    assert (await bob.get("/api/connections")).json()["connections"][0]["account"] == EMAIL_B
    # Disconnecting one leaves the other.
    assert (await bob.call("DELETE", CONNECT)).status_code == 204
    assert await owned(env, "SELECT count(*) FROM connections", "alice@example.com") == 1
    assert await owned(env, "SELECT count(*) FROM connections", "bob@example.com") == 0


async def test_a_401_on_use_flips_to_needs_reconnect_and_reconnect_keeps_the_id(
    env: Env, make_actor: Any, swedebeat_api: FakeSwedebeat
) -> None:
    actor = await actor_for(env, make_actor)
    assert (await actor.post(CONNECT, body())).status_code == 200
    first_id = await owned(env, "SELECT id FROM connections")
    ctx, ring = await user_ctx(env), env.app.state.keyring
    await connections.use(env.factory, ring, ctx, "swedebeat", catalogue_search)
    swedebeat_api.tokens.clear()  # Swedebeat forgot the stored token
    with pytest.raises(ConnectionFailure) as caught:
        await connections.use(env.factory, ring, ctx, "swedebeat", catalogue_search)
    assert caught.value.category == "needs_reconnect"
    shown = (await actor.get("/api/connections")).json()["connections"][0]
    assert (shown["status"], shown["last_error"], shown["account"]) == (
        "needs_reconnect", "needs_reconnect", EMAIL,
    )  # fmt: skip
    again = await actor.post(CONNECT, body())  # same device: no force needed
    assert (again.status_code, again.json()["status"]) == (200, "connected")
    assert await owned(env, "SELECT id FROM connections") == first_id
    await connections.use(env.factory, ring, ctx, "swedebeat", catalogue_search)


async def test_connect_and_disconnect_need_recent_auth_and_step_up(
    env: Env, make_actor: Any, swedebeat_api: FakeSwedebeat
) -> None:
    actor = await actor_for(env, make_actor)
    assert (await actor.post(CONNECT, body())).status_code == 200
    await env.run("UPDATE sessions SET reauth_at = now() - interval '11 minutes'")
    for call in (actor.post(CONNECT, body()), actor.call("DELETE", CONNECT)):
        refused = await call
        assert (refused.status_code, refused.json()["detail"]["reason"]) == (403, "reauth_required")
    assert len(swedebeat_api.logins) == 1
    await env.run("UPDATE sessions SET reauth_at = now()")
    await actor.enroll()
    await env.run("UPDATE sessions SET stepup_at = NULL")
    stepped = await actor.post(CONNECT, body())
    assert (stepped.status_code, stepped.json()["detail"]["reason"]) == (403, "stepup_required")
    gone = await actor.call("DELETE", CONNECT)
    assert (gone.status_code, gone.json()["detail"]["reason"]) == (403, "stepup_required")
    assert len(swedebeat_api.logins) == 1


async def test_anonymous_and_cross_site_requests_are_refused_before_swedebeat(
    env: Env, swedebeat_api: FakeSwedebeat
) -> None:
    from classbeat.auth.cookies import CSRF_COOKIE

    assert (
        await env.client.post(CONNECT, json=body(), headers={"Origin": "https://evil.test"})
    ).status_code == 403
    csrf = {"Origin": ORIGIN, "X-CSRF-Token": "t" * 32, "Cookie": f"{CSRF_COOKIE}={'t' * 32}"}
    assert (await env.client.post(CONNECT, json=body(), headers=csrf)).status_code == 401
    assert (await env.client.request("DELETE", CONNECT, headers=csrf)).status_code == 401
    env.client.cookies.clear()
    assert swedebeat_api.logins == []


async def test_connect_is_rate_limited_per_swedebeat_email_like_mowl(
    env: Env, make_actor: Any, swedebeat_api: FakeSwedebeat
) -> None:
    actor = await actor_for(env, make_actor)
    for _ in range(5):
        assert (await actor.post(CONNECT, body(password="wrong"))).status_code == 400
    limited = await actor.post(CONNECT, body(password="wrong"))
    assert limited.status_code == 429 and "Retry-After" in limited.headers  # noqa: PT018
    assert len(swedebeat_api.logins) == 5  # the sixth never reached Swedebeat


async def test_events_hold_the_provider_and_category_and_no_secret(
    env: Env, make_actor: Any, swedebeat_api: FakeSwedebeat
) -> None:
    swedebeat_api.hold(EMAIL, "phone-device-0001")
    actor = await actor_for(env, make_actor)
    assert (await actor.post(CONNECT, body())).status_code == 409
    assert (await actor.post(CONNECT, body(force=True))).status_code == 200
    assert (await actor.call("DELETE", CONNECT)).status_code == 204
    rows: Any = await env.scalar(
        "SELECT json_agg(json_build_object('kind', kind, 'detail', detail) ORDER BY at, id) "
        "FROM auth_events WHERE kind LIKE 'connection_%'"
    )
    assert [(r["kind"], r["detail"]) for r in rows] == [
        ("connection_failed", {"provider": "swedebeat", "category": "device_in_use"}),
        ("connection_connected", {"provider": "swedebeat"}),
        ("connection_disconnected", {"provider": "swedebeat"}),
    ]
    dump = json.dumps(rows) + str(await env.scalar("SELECT json_agg(detail) FROM auth_events"))
    assert PASSWORD not in dump and all(t not in dump for t in swedebeat_api.tokens)  # noqa: PT018


async def test_invalid_bodies_never_echo_the_password(env: Env, make_actor: Any) -> None:
    actor = await actor_for(env, make_actor)
    for sent in (
        {"email": "not an email", "password": PASSWORD},
        {"email": EMAIL, "password": PASSWORD * 100},
        {"password": PASSWORD},
        {"email": EMAIL, "password": ""},
        {"email": EMAIL, "password": PASSWORD, "force": "yes please"},
    ):
        response = await actor.post(CONNECT, sent)
        assert response.status_code == 422
        assert PASSWORD not in response.text
        assert '"input"' not in response.text  # the key pydantic echoes the sent value under


async def test_the_other_providers_ignore_swedebeat_and_it_is_optional(
    env: Env, make_actor: Any
) -> None:
    """Nothing else requires Swedebeat: a user with none lists no connection and no error."""
    actor = await actor_for(env, make_actor)
    listed = (await actor.get("/api/connections")).json()
    assert listed["connections"] == []
    assert listed["swedebeat"]["enabled"] is True
