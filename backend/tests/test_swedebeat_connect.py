"""SwedeBeat connect, status and disconnect through the API (ADR 0035 sections 3 to 5; ADR 0015
for what stays), against the in-process fake gateway (``gatewaycore`` over an httpx transport,
behind ``swedebeat_gateway.opener``). Never the network, never SwedeBeat."""

import json
import logging
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import httpx
import pytest
from pydantic import SecretStr
from sqlalchemy import Connection, text

from authtools import ORIGIN, Env, running_env
from classbeat import connections
from classbeat.connections import ConnectionFailure
from classbeat.connections.credentials import build_credentials
from classbeat.connections.swedebeat_account import account_ref_for, device_id_for, open_bearer
from classbeat.db import user_session
from classbeat.egress_gateway import LOGIN_SECONDS, swedebeat_gateway_test_client
from classbeat.swedebeat_gateway import opener
from ctx import make_context
from gatewaycore import LOGIN_PATH, TTL_SECONDS, FakeGateway
from gatewayfake import (
    CLIENT_KEY,
    EMAIL,
    EMAIL_B,
    GATEWAY_SETTINGS,
    PASSWORD,
    PASSWORD_B,
    calls,
    gateway_client,
    store_legacy_row,
)
from gatewayfake import ORIGIN as GATEWAY_ORIGIN
from preftools import use_swedebeat

CONNECT = "/api/connections/swedebeat"
REFUSED_KEY = "swedebeat gateway refused the client key"
pytestmark = pytest.mark.usefixtures("gateway_api", "frozen_rate_windows")  # #1012
USER = "user@example.com"


@pytest.fixture
async def env(clean_auth: None, app_database_url: str) -> AsyncIterator[Env]:
    """The app with SwedeBeat switched on and the fake gateway's URL and key configured."""
    async with running_env(app_database_url, swedebeat_enabled=True, **GATEWAY_SETTINGS) as running:
        yield running


async def actor_for(env: Env, make_actor: Any, email: str = USER) -> Any:
    user_id = await env.user(email)
    await use_swedebeat(env.factory, user_id)  # #525: the user's own switch, default off
    return await make_actor(user_id)


async def user_id(env: Env, email: str = USER) -> UUID:
    found = await env.scalar("SELECT id FROM users WHERE email = :e", e=email)
    assert isinstance(found, UUID)
    return found


async def user_ctx(env: Env, email: str = USER) -> Any:
    return make_context(await user_id(env, email))


async def owned(env: Env, sql: str, email: str = USER) -> Any:
    async with user_session(env.factory, await user_ctx(env, email)) as db:
        return (await db.execute(text(sql))).scalar_one_or_none()


async def write_own(env: Env, sql: str, email: str = USER) -> None:
    """A write to the user's own row (``connections`` is under owner row security)."""
    async with user_session(env.factory, await user_ctx(env, email)) as db:
        assert (await db.execute(text(sql))).rowcount == 1  # pyright: ignore[reportAttributeAccessIssue]


def body(email: str = EMAIL, password: str = PASSWORD, **more: Any) -> dict[str, Any]:
    return {"email": email, "password": password, "force": True, **more}


def secret_of(env: Env) -> bytes:
    key = env.settings.app_secret_key
    assert key is not None
    return key.get_secret_value().encode()


async def bearer_of(env: Env, email: str = USER) -> str:
    opened = await open_bearer(env.factory, env.app.state.keyring, await user_ctx(env, email))
    return opened.bearer


def logins(fake: FakeGateway) -> list[Any]:
    return calls(fake, LOGIN_PATH)


async def test_connect_logs_in_with_force_and_stores_only_the_bearer_encrypted(
    env: Env, make_actor: Any, gateway_api: FakeGateway
) -> None:
    actor = await actor_for(env, make_actor)
    before = datetime.now(UTC)
    response = await actor.post(CONNECT, body())
    assert response.status_code == 200, response.text
    shown = response.json()
    assert (shown["provider"], shown["status"], shown["last_error"], shown["account"]) == (
        "swedebeat", "connected", None, EMAIL,
    )  # fmt: skip
    # One login: the user's HMAC device id, force true, the key and the JSON headers only.
    [login] = logins(gateway_api)
    device_id = device_id_for(secret_of(env), await user_id(env))
    assert login.body == {
        "email": EMAIL, "password": PASSWORD, "device_id": device_id, "force": True,
    }  # fmt: skip
    assert login.method == "POST"
    assert login.headers["x-api-key"] == CLIENT_KEY
    assert login.headers["content-type"] == login.headers["accept"] == "application/json"
    assert "authorization" not in login.headers
    [issued] = gateway_api.bearers
    # The payload is exactly {bearer}, encrypted; expires_at from expires_in; public {email}.
    assert await bearer_of(env) == issued
    secret = await owned(env, "SELECT secret FROM connections")
    assert isinstance(secret, bytes) and issued.encode() not in secret  # noqa: PT018
    assert device_id.encode() not in secret
    expires = await owned(env, "SELECT expires_at FROM connections")
    ttl = timedelta(seconds=TTL_SECONDS)
    assert before + ttl - timedelta(seconds=5) <= expires <= datetime.now(UTC) + ttl
    assert json.loads(await owned(env, "SELECT public::text FROM connections")) == {"email": EMAIL}
    assert PASSWORD not in response.text and issued not in response.text  # noqa: PT018
    listed = (await actor.get("/api/connections")).json()
    assert [(c["provider"], c["status"], c["account"]) for c in listed["connections"]] == [
        ("swedebeat", "connected", EMAIL)
    ]
    assert listed["swedebeat"] == {"enabled": True, "available": True, "uses_swedebeat": True}


async def test_the_bearer_is_never_library_credentials(env: Env, make_actor: Any) -> None:
    actor = await actor_for(env, make_actor)
    assert (await actor.post(CONNECT, body())).status_code == 200
    with pytest.raises(ValueError, match="never library credentials"):
        build_credentials("swedebeat", {"bearer": await bearer_of(env)}, {})
    with pytest.raises(ConnectionFailure) as caught:
        await connections.open_connection(
            env.factory, env.app.state.keyring, await user_ctx(env), "swedebeat"
        )
    assert caught.value.category == "unknown"


async def test_a_body_without_force_true_is_409_before_the_rate_limit_and_sends_nothing(
    env: Env, make_actor: Any, gateway_api: FakeGateway
) -> None:
    actor = await actor_for(env, make_actor)
    for sent in ({"email": EMAIL, "password": PASSWORD}, body(force=False)):
        refused = await actor.post(CONNECT, sent)
        assert (refused.status_code, refused.json()) == (
            409, {"detail": {"reason": "sign_out_required"}},
        )  # fmt: skip
    # Strict: no other value is the consent, and none of them is sent either.
    for value in ("true", 1, "yes"):
        assert (await actor.post(CONNECT, body(force=value))).status_code == 422
    # No rate-limit slot was spent on a request that sent nothing: 10 more refusals, no 429.
    for _ in range(10):
        assert (await actor.post(CONNECT, body(force=False))).status_code == 409
    assert gateway_api.requests == []
    assert await owned(env, "SELECT count(*) FROM connections") == 0


def _injected(fake: FakeGateway, kind: str, **params: Any) -> None:
    fake.inject(kind, LOGIN_PATH, **params)


# Every row of ADR 0035 section 3's table that the fake gateway can answer: what it answers and
# the route's status and reason.
GATEWAY_ANSWERS = [
    ("unknown_client", {}, 503, "app_key_unavailable"),
    ("status", {"status": 403}, 503, "app_key_unavailable"),
    ("status", {"status": 422}, 400, "invalid_credentials"),
    ("status", {"status": 413}, 502, "unknown"),
    ("status", {"status": 404}, 502, "unknown"),
    ("status", {"status": 409}, 502, "unknown"),
    ("redirect", {"status": 302, "location": "/auth/other"}, 502, "unknown"),
    ("redirect", {"status": 307, "location": "https://elsewhere.example/x"}, 502, "unknown"),
    ("body", {"body": b"not json"}, 502, "unknown"),
    ("body", {"body": b'{"bearer": "short", "expires_in": 86400}'}, 502, "unknown"),
    ("body", {"body": b'{"bearer": "' + b"b" * 32 + b'", "expires_in": 5}'}, 502, "unknown"),
    ("oversize", {"size": 20 * 1024}, 502, "unknown"),
    ("gzip", {}, 502, "unknown"),
    ("status", {"status": 502}, 503, "upstream_unavailable"),
    ("status", {"status": 500}, 503, "upstream_unavailable"),
    ("status", {"status": 503}, 503, "upstream_unavailable"),
]


@pytest.mark.parametrize(("kind", "params", "status", "reason"), GATEWAY_ANSWERS)
async def test_each_gateway_answer_has_its_connect_result_and_stores_nothing(
    env: Env,
    make_actor: Any,
    gateway_api: FakeGateway,
    kind: str,
    params: dict[str, Any],
    status: int,
    reason: str,
) -> None:
    actor = await actor_for(env, make_actor)
    _injected(gateway_api, kind, **params)
    response = await actor.post(CONNECT, body())
    assert (response.status_code, response.json()) == (status, {"detail": {"reason": reason}})
    assert len(logins(gateway_api)) == 1
    assert await owned(env, "SELECT count(*) FROM connections") == 0
    assert PASSWORD not in response.text


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


async def test_a_429_is_rate_limited_with_the_gateway_retry_after(
    env: Env, make_actor: Any, gateway_api: FakeGateway
) -> None:
    actor = await actor_for(env, make_actor)
    _injected(gateway_api, "rate_limited", retry_after="42")
    response = await actor.post(CONNECT, body())
    assert (response.status_code, response.json()["detail"]["reason"]) == (429, "rate_limited")
    assert response.headers["Retry-After"] == "42"


def _failing_opener(error: Exception) -> Any:
    def handler(request: httpx.Request) -> httpx.Response:
        raise error

    def open_client(total_seconds: float) -> httpx.AsyncClient:
        transport = httpx.MockTransport(handler)
        return swedebeat_gateway_test_client(GATEWAY_ORIGIN, transport, total_seconds=total_seconds)

    return open_client


@pytest.mark.parametrize(
    "error",
    [
        httpx.ConnectError("refused"),
        httpx.ConnectTimeout("timeout"),
        httpx.ReadTimeout("timeout"),
        httpx.RemoteProtocolError("closed"),
    ],
)
async def test_a_refused_connection_or_a_timeout_is_upstream_unavailable(
    env: Env, make_actor: Any, monkeypatch: pytest.MonkeyPatch, error: Exception
) -> None:
    monkeypatch.setattr(opener, "open_client", _failing_opener(error))
    actor = await actor_for(env, make_actor)
    response = await actor.post(CONNECT, body())
    assert (response.status_code, response.json()["detail"]["reason"]) == (
        503, "upstream_unavailable",
    )  # fmt: skip
    assert PASSWORD not in response.text


async def test_the_deadline_is_upstream_unavailable(
    env: Env, make_actor: Any, gateway_api: FakeGateway, monkeypatch: pytest.MonkeyPatch
) -> None:
    asked: list[float] = []

    def open_client(total_seconds: float) -> httpx.AsyncClient:
        asked.append(total_seconds)
        return gateway_client(gateway_api, total_seconds=1)

    monkeypatch.setattr(opener, "open_client", open_client)
    _injected(gateway_api, "slow", seconds=2.0)
    actor = await actor_for(env, make_actor)
    response = await actor.post(CONNECT, body())
    assert (response.status_code, response.json()["detail"]["reason"]) == (
        503, "upstream_unavailable",
    )  # fmt: skip
    assert asked == [LOGIN_SECONDS]  # the login asks for its own 30 s


async def test_unknown_client_releases_the_slot_logs_the_category_and_never_flips_a_row(
    env: Env, make_actor: Any, gateway_api: FakeGateway, caplog: pytest.LogCaptureFixture
) -> None:
    actor = await actor_for(env, make_actor)
    assert (await actor.post(CONNECT, body())).status_code == 200
    caplog.set_level(logging.INFO)
    for _ in range(8):  # more than the five a user gets: each slot was given back
        _injected(gateway_api, "unknown_client")
        refused = await actor.post(CONNECT, body())
        assert (refused.status_code, refused.json()) == (
            503, {"detail": {"reason": "app_key_unavailable"}},
        )  # fmt: skip
    # The stored connection is untouched, and the user is not asked for a password.
    assert await owned(env, "SELECT status FROM connections") == "connected"
    assert await owned(env, "SELECT last_error FROM connections") is None
    lines = [
        r for r in caplog.records if r.getMessage() == "swedebeat gateway refused the client key"
    ]
    assert len(lines) == 8
    assert {(r.levelno, getattr(r, "category", None)) for r in lines} == {
        (logging.ERROR, "app_key_unavailable")
    }
    assert CLIENT_KEY not in caplog.text and PASSWORD not in caplog.text  # noqa: PT018


async def test_without_an_installed_origin_connect_logs_not_configured_and_sends_nothing(
    env: Env,
    make_actor: Any,
    gateway_api: FakeGateway,
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The settings name a gateway, but this process installed no origin (the production builder
    refuses before anything is built): the same 503, and the operator line says the gateway is not
    configured, never that it refused the key (nothing was sent)."""
    from classbeat import egress_gateway

    actor = await actor_for(env, make_actor)
    monkeypatch.setattr(egress_gateway, "_origin", None)
    monkeypatch.setattr(opener, "open_client", egress_gateway.swedebeat_gateway_async_client)
    caplog.set_level(logging.INFO)
    refused = await actor.post(CONNECT, body())
    assert (refused.status_code, refused.json()) == (
        503, {"detail": {"reason": "app_key_unavailable"}},
    )  # fmt: skip
    lines = [r for r in caplog.records if r.getMessage() == "swedebeat gateway not configured"]
    assert [(r.levelno, getattr(r, "category", None)) for r in lines] == [
        (logging.ERROR, "app_key_unavailable")
    ]
    assert not [r for r in caplog.records if r.getMessage() == REFUSED_KEY]
    assert gateway_api.requests == []
    assert await owned(env, "SELECT count(*) FROM connections") == 0
    assert CLIENT_KEY not in caplog.text and PASSWORD not in caplog.text  # noqa: PT018


async def test_without_the_url_or_the_key_connect_says_so_and_sends_nothing(
    env: Env, make_actor: Any, gateway_api: FakeGateway
) -> None:
    actor = await actor_for(env, make_actor)
    for missing in ({"swedebeat_gateway_key": None}, {"swedebeat_gateway_url": None}):
        env.app.state.settings = env.settings.model_copy(update=missing)
        response = await actor.post(CONNECT, body())
        assert (response.status_code, response.json()) == (
            503, {"detail": {"reason": "app_key_unavailable"}},
        )  # fmt: skip
        assert (await actor.get("/api/connections")).json()["swedebeat"] == {
            "enabled": True, "available": False, "uses_swedebeat": True,
        }  # fmt: skip
        # No rate-limit slot was spent on a request that sent nothing: 10 more, no 429.
        for _ in range(10):
            assert (await actor.post(CONNECT, body())).status_code == 503
    assert gateway_api.requests == []


async def test_an_empty_key_means_unavailable(env: Env, make_actor: Any) -> None:
    env.app.state.settings = env.settings.model_copy(
        update={"swedebeat_gateway_key": SecretStr("")}
    )
    actor = await actor_for(env, make_actor)
    assert (await actor.get("/api/connections")).json()["swedebeat"]["available"] is False


async def test_a_legacy_token_row_opens_as_needs_reconnect_without_a_request(
    env: Env, make_actor: Any, gateway_api: FakeGateway
) -> None:
    actor = await actor_for(env, make_actor)
    ctx = await user_ctx(env)
    row_id = await store_legacy_row(env.factory, env.app.state.keyring, ctx)
    # The page already asks for a reconnect: the row has no expires_at.
    shown = (await actor.get("/api/connections")).json()["connections"][0]
    assert (shown["status"], shown["account"]) == ("needs_reconnect", EMAIL)
    with pytest.raises(ConnectionFailure) as caught:
        await open_bearer(env.factory, env.app.state.keyring, ctx)
    assert caught.value.category == "needs_reconnect"
    assert await owned(env, "SELECT status FROM connections") == "needs_reconnect"
    assert await owned(env, "SELECT last_error FROM connections") == "needs_reconnect"
    assert gateway_api.requests == []
    # A reconnect replaces it in place with a bearer.
    again = await actor.post(CONNECT, body())
    assert (again.status_code, again.json()["status"]) == (200, "connected")
    assert await owned(env, "SELECT id FROM connections") == row_id
    assert await bearer_of(env) in gateway_api.bearers


@pytest.mark.parametrize("expires_at", ["now() - interval '1 second'", "NULL"])
async def test_a_bearer_past_or_without_expires_at_shows_needs_reconnect_and_never_opens(
    env: Env, make_actor: Any, gateway_api: FakeGateway, expires_at: str
) -> None:
    actor = await actor_for(env, make_actor)
    assert (await actor.post(CONNECT, body())).status_code == 200
    expires = await owned(env, "SELECT expires_at FROM connections")
    opened = await open_bearer(
        env.factory, env.app.state.keyring, await user_ctx(env), now=expires - timedelta(seconds=1)
    )
    assert opened.expires_at == expires
    assert opened.bearer not in repr(opened)
    await write_own(env, f"UPDATE connections SET expires_at = {expires_at}")  # noqa: S608
    shown = (await actor.get("/api/connections")).json()["connections"][0]
    assert (shown["status"], shown["last_error"]) == ("needs_reconnect", None)
    with pytest.raises(ConnectionFailure) as caught:
        await open_bearer(env.factory, env.app.state.keyring, await user_ctx(env))
    assert caught.value.category == "not_connected"
    assert await owned(env, "SELECT status FROM connections") == "connected"  # no write
    assert len(gateway_api.requests) == 1  # the connect's login, nothing since


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
    env: Env, make_actor: Any, gateway_api: FakeGateway, superuser: Connection
) -> None:
    actor = await actor_for(env, make_actor)
    assert (await actor.post(CONNECT, body())).status_code == 200
    store_flag(superuser, "off")
    refused = await actor.post(CONNECT, body())
    assert (refused.status_code, refused.json()) == (404, {"detail": {"reason": "feature_off"}})
    assert len(logins(gateway_api)) == 1  # nothing reached the gateway the second time
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
    env: Env, make_actor: Any, gateway_api: FakeGateway
) -> None:
    actor = await actor_for(env, make_actor)
    other = await actor_for(env, make_actor, "other@example.com")
    secret = secret_of(env)
    first = device_id_for(secret, await user_id(env))
    assert len(first) == 16
    assert (await actor.post(CONNECT, body())).status_code == 200
    assert (await actor.post(CONNECT, body())).status_code == 200  # a reconnect
    assert (await actor.call("DELETE", CONNECT)).status_code == 204
    assert (await actor.post(CONNECT, body())).status_code == 200  # after a disconnect
    assert (await other.post(CONNECT, body(EMAIL_B, PASSWORD_B))).status_code == 200
    second = device_id_for(secret, await user_id(env, "other@example.com"))
    assert second != first
    sent = [(r.body["email"], r.body["device_id"]) for r in logins(gateway_api)]
    assert sent == [(EMAIL, first)] * 3 + [(EMAIL_B, second)]


async def test_two_users_carry_their_own_bearers(
    env: Env, make_actor: Any, gateway_api: FakeGateway
) -> None:
    alice = await actor_for(env, make_actor, "alice@example.com")
    bob = await actor_for(env, make_actor, "bob@example.com")
    assert (await alice.post(CONNECT, body())).status_code == 200
    assert (await bob.post(CONNECT, body(EMAIL_B, PASSWORD_B))).status_code == 200
    a, b = await bearer_of(env, "alice@example.com"), await bearer_of(env, "bob@example.com")
    assert a != b
    assert (gateway_api.bearers[a].email, gateway_api.bearers[b].email) == (EMAIL, EMAIL_B)
    assert (await alice.get("/api/connections")).json()["connections"][0]["account"] == EMAIL
    assert (await bob.get("/api/connections")).json()["connections"][0]["account"] == EMAIL_B
    # Disconnecting one leaves the other.
    assert (await bob.call("DELETE", CONNECT)).status_code == 204
    assert await owned(env, "SELECT count(*) FROM connections", "alice@example.com") == 1
    assert await owned(env, "SELECT count(*) FROM connections", "bob@example.com") == 0


async def test_a_reconnect_keeps_the_row_id_and_replaces_the_bearer(
    env: Env, make_actor: Any, gateway_api: FakeGateway
) -> None:
    actor = await actor_for(env, make_actor)
    assert (await actor.post(CONNECT, body())).status_code == 200
    first_id, first = await owned(env, "SELECT id FROM connections"), await bearer_of(env)
    await write_own(env, "UPDATE connections SET status = 'needs_reconnect'")
    again = await actor.post(CONNECT, body())
    assert (again.status_code, again.json()["status"]) == (200, "connected")
    assert await owned(env, "SELECT id FROM connections") == first_id
    assert await bearer_of(env) != first


async def test_every_connect_stores_the_accounts_digest_and_never_keeps_an_old_one(
    env: Env, make_actor: Any, gateway_api: FakeGateway, caplog: pytest.LogCaptureFixture
) -> None:
    """#1117: ``account_ref`` is the keyed digest of the login's ``customer_id`` (never the raw
    id, never logged), the same on a reconnect to the same account, another for another account,
    and ``NULL`` after a login that names none: a reconnect never keeps the old account."""
    caplog.set_level(logging.DEBUG)
    actor = await actor_for(env, make_actor)
    me = await user_id(env)
    secret = secret_of(env)
    sql = "SELECT account_ref FROM connections"
    assert (await actor.post(CONNECT, body())).status_code == 200
    first = await owned(env, sql)
    expected = account_ref_for(secret, me, gateway_api.customer_of(EMAIL))
    assert isinstance(first, str)
    assert first == expected
    assert len(first) == 32
    opened = await open_bearer(env.factory, env.app.state.keyring, await user_ctx(env))
    assert opened.account_ref == first
    assert first not in repr(opened)
    assert (await actor.post(CONNECT, body())).status_code == 200  # the same account again
    assert await owned(env, sql) == first
    assert (await actor.post(CONNECT, body(EMAIL_B, PASSWORD_B))).status_code == 200
    other = await owned(env, sql)
    assert isinstance(other, str)
    assert other != first
    gateway_api.answers_customer = False
    assert (await actor.post(CONNECT, body())).status_code == 200  # still connects
    assert await owned(env, sql) is None  # never the old account's digest
    # Per user: another user's digest of the same account differs.
    gateway_api.answers_customer = True
    assert account_ref_for(secret, UUID(int=1), gateway_api.customer_of(EMAIL)) != first
    customers = {gateway_api.customer_of(EMAIL), gateway_api.customer_of(EMAIL_B)}
    shown = json.dumps((await actor.get("/api/connections")).json())
    for value in customers | {first, other}:
        assert value not in shown
        assert value not in caplog.text
    # It goes with the row.
    assert (await actor.call("DELETE", CONNECT)).status_code == 204
    assert await owned(env, "SELECT count(*) FROM connections") == 0


async def test_only_a_swedebeat_row_holds_an_account_ref(
    env: Env, make_actor: Any, gateway_api: FakeGateway
) -> None:
    actor = await actor_for(env, make_actor)
    ctx = await user_ctx(env)
    with pytest.raises(ValueError, match="only a SwedeBeat connection"):
        await connections.save(
            env.factory, env.app.state.keyring, ctx, "player", {"token": "t"}, account_ref="0" * 32
        )
    assert (await actor.post(CONNECT, body())).status_code == 200
    for bad in ("A" * 32, "0" * 31, "0" * 64, "35907"):
        with pytest.raises(Exception, match="connections_account_ref"):
            await write_own(env, f"UPDATE connections SET account_ref = '{bad}'")  # noqa: S608


async def test_connect_and_disconnect_need_recent_auth_and_step_up(
    env: Env, make_actor: Any, gateway_api: FakeGateway
) -> None:
    actor = await actor_for(env, make_actor)
    assert (await actor.post(CONNECT, body())).status_code == 200
    await env.run("UPDATE sessions SET reauth_at = now() - interval '11 minutes'")
    for call in (actor.post(CONNECT, body()), actor.call("DELETE", CONNECT)):
        refused = await call
        assert (refused.status_code, refused.json()["detail"]["reason"]) == (403, "reauth_required")
    assert len(logins(gateway_api)) == 1
    await env.run("UPDATE sessions SET reauth_at = now()")
    await actor.enroll()
    await env.run("UPDATE sessions SET stepup_at = NULL")
    stepped = await actor.post(CONNECT, body())
    assert (stepped.status_code, stepped.json()["detail"]["reason"]) == (403, "stepup_required")
    gone = await actor.call("DELETE", CONNECT)
    assert (gone.status_code, gone.json()["detail"]["reason"]) == (403, "stepup_required")
    assert len(logins(gateway_api)) == 1


async def test_anonymous_and_cross_site_requests_are_refused_before_the_gateway(
    env: Env, gateway_api: FakeGateway
) -> None:
    from classbeat.auth.cookies import CSRF_COOKIE

    assert (
        await env.client.post(CONNECT, json=body(), headers={"Origin": "https://evil.test"})
    ).status_code == 403
    csrf = {"Origin": ORIGIN, "X-CSRF-Token": "t" * 32, "Cookie": f"{CSRF_COOKIE}={'t' * 32}"}
    assert (await env.client.post(CONNECT, json=body(), headers=csrf)).status_code == 401
    assert (await env.client.request("DELETE", CONNECT, headers=csrf)).status_code == 401
    env.client.cookies.clear()
    assert gateway_api.requests == []


async def test_connect_is_rate_limited_per_swedebeat_email_like_mowl(
    env: Env, make_actor: Any, gateway_api: FakeGateway
) -> None:
    actor = await actor_for(env, make_actor)
    for _ in range(5):
        assert (await actor.post(CONNECT, body(password="wrong"))).status_code == 400
    limited = await actor.post(CONNECT, body(password="wrong"))
    assert limited.status_code == 429 and "Retry-After" in limited.headers  # noqa: PT018
    assert len(logins(gateway_api)) == 5  # the sixth never reached the gateway


async def test_events_hold_the_provider_and_category_and_no_secret(
    env: Env, make_actor: Any, gateway_api: FakeGateway
) -> None:
    actor = await actor_for(env, make_actor)
    _injected(gateway_api, "unknown_client")
    assert (await actor.post(CONNECT, body())).status_code == 503
    assert (await actor.post(CONNECT, body(password="wrong"))).status_code == 400
    assert (await actor.post(CONNECT, body())).status_code == 200
    assert (await actor.call("DELETE", CONNECT)).status_code == 204
    rows: Any = await env.scalar(
        "SELECT json_agg(json_build_object('kind', kind, 'detail', detail) ORDER BY at, id) "
        "FROM auth_events WHERE kind LIKE 'connection_%'"
    )
    assert [(r["kind"], r["detail"]) for r in rows] == [
        ("connection_failed", {"provider": "swedebeat", "category": "app_key_unavailable"}),
        ("connection_failed", {"provider": "swedebeat", "category": "invalid_credentials"}),
        ("connection_connected", {"provider": "swedebeat"}),
        ("connection_disconnected", {"provider": "swedebeat"}),
    ]
    dump = json.dumps(rows) + str(await env.scalar("SELECT json_agg(detail) FROM auth_events"))
    assert PASSWORD not in dump and all(t not in dump for t in gateway_api.bearers)  # noqa: PT018
    assert CLIENT_KEY not in dump


async def test_invalid_bodies_never_echo_the_password_and_send_nothing(
    env: Env, make_actor: Any, gateway_api: FakeGateway
) -> None:
    actor = await actor_for(env, make_actor)
    for sent in (
        {"email": "not an email", "password": PASSWORD, "force": True},
        {"email": EMAIL, "password": PASSWORD * 100, "force": True},
        {"email": EMAIL, "password": "p" * 257, "force": True},  # over the gateway's 256
        {"password": PASSWORD, "force": True},
        {"email": EMAIL, "password": "", "force": True},
        {"email": EMAIL, "password": PASSWORD, "force": "yes please"},
    ):
        response = await actor.post(CONNECT, sent)
        assert response.status_code == 422
        assert PASSWORD not in response.text
        assert '"input"' not in response.text  # the key pydantic echoes the sent value under
    assert gateway_api.requests == []


async def test_the_other_providers_ignore_swedebeat_and_it_is_optional(
    env: Env, make_actor: Any
) -> None:
    """Nothing else requires Swedebeat: a user with none lists no connection and no error."""
    actor = await actor_for(env, make_actor)
    listed = (await actor.get("/api/connections")).json()
    assert listed["connections"] == []
    assert listed["swedebeat"]["enabled"] is True
