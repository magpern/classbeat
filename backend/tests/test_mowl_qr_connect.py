"""``/api/connections/mowl/qr`` (ADR 0024, #647) through the API: start a ticket, serve its image
from our origin, poll until the phone has scanned, and store the token as the password connect
does. Against the fake MOWL API (``mowlfake``), the fake image host and the fake loopback broker
(``mowlqrfake``); never the network."""

import asyncio
import json
import logging
from typing import Any
from uuid import UUID

import pytest
from mowl.credentials import Credentials as LibraryCredentials
from sqlalchemy import text

from authtools import Env
from classbeat import connections
from classbeat.db import user_session
from ctx import make_context
from mowlfake import FakeMowl
from mowlqrfake import KEY, MQTT_PASSWORD, PNG, QR_TOKEN, TICKET_ID, FakeBroker, FakeImageHost

QR = "/api/connections/mowl/qr"
USER = "user@example.com"
SECRETS = (KEY, MQTT_PASSWORD, QR_TOKEN)
pytestmark = pytest.mark.usefixtures("mowl_api")


@pytest.fixture
def qr_env(env: Env) -> Env:
    """The ``env`` app (the one ``make_actor`` talks to) with the switch on."""
    settings = env.settings.model_copy(update={"mowl_qr_enabled": True})
    env.app.state.settings = settings
    return Env(env.app, env.client, settings)


async def poll(actor: Any, ticket_id: str, until: str, seconds: float = 5.0) -> dict[str, Any]:
    for _ in range(int(seconds / 0.05)):
        response = await actor.get(f"{QR}/{ticket_id}")
        assert response.status_code == 200, response.text
        shown = response.json()
        if shown["status"] == until:
            return shown
        await asyncio.sleep(0.05)
    raise AssertionError(f"never {until}")


async def owned(env: Env, sql: str, email: str = USER) -> Any:
    user_id = await env.scalar("SELECT id FROM users WHERE email = :e", e=email)
    assert isinstance(user_id, UUID)
    async with user_session(env.factory, make_context(user_id)) as db:
        return (await db.execute(text(sql))).scalar_one_or_none()


async def events(env: Env) -> list[tuple[str, dict[str, Any]]]:
    sql = "SELECT kind, detail FROM auth_events WHERE kind LIKE 'connection_%' ORDER BY at, id"
    async with env.db() as db:
        return [(row.kind, row.detail) for row in (await db.execute(text(sql))).all()]


async def test_scan_connects_and_stores_the_token_encrypted(
    qr_env: Env,
    make_actor: Any,
    mowl_api: FakeMowl,
    image_host: FakeImageHost,
    broker: FakeBroker,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    env = qr_env
    actor = await make_actor(await env.user(USER))
    started = await actor.post(QR)
    assert started.status_code == 200, started.text
    body = started.json()
    assert set(body) == {"ticket_id", "image", "expires_at"}
    ticket_id = body["ticket_id"]
    assert body["image"] == f"{QR}/{ticket_id}/image"
    assert str(TICKET_ID) not in ticket_id
    assert all(secret not in started.text for secret in SECRETS)
    assert mowl_api.qr_authorizations == [None]  # anonymous: no authorization header

    image = await actor.get(body["image"])
    assert (image.status_code, image.content) == (200, PNG)
    assert image.headers["content-type"] == "image/png"
    assert image.headers["cache-control"] == "no-store"
    assert image.headers["x-content-type-options"] == "nosniff"

    await broker.subscribed()
    assert broker.subscriptions == [f"qrticket/{KEY}"]
    assert (await poll(actor, ticket_id, "waiting"))["connection"] is None
    broker.scan()
    shown = await poll(actor, ticket_id, "connected")
    assert all(secret not in json.dumps(shown) for secret in SECRETS)
    assert (shown["connection"]["provider"], shown["connection"]["status"]) == ("mowl", "connected")
    assert (await actor.get(body["image"])).status_code == 404  # only while it waits

    secret = await owned(env, "SELECT secret FROM connections")
    assert isinstance(secret, bytes) and QR_TOKEN.encode() not in secret  # noqa: PT018
    user_id = await env.scalar("SELECT id FROM users WHERE email = :e", e=USER)
    assert isinstance(user_id, UUID)
    opened = await connections.open_connection(
        env.factory, env.app.state.keyring, make_context(user_id), "mowl"
    )
    assert isinstance(opened.credentials, LibraryCredentials)
    assert opened.credentials.mowl_token == QR_TOKEN
    assert await events(env) == [("connection_connected", {"provider": "mowl", "method": "qr"})]
    logged = caplog.text + "".join(repr(r.__dict__) for r in caplog.records)
    assert all(secret not in logged for secret in SECRETS)


async def test_the_switch_off_answers_feature_off(
    env: Env, make_actor: Any, mowl_api: FakeMowl
) -> None:
    actor = await make_actor(await env.user(USER))
    response = await actor.post(QR)
    assert (response.status_code, response.json()) == (404, {"detail": {"reason": "feature_off"}})
    assert (await actor.get(f"{QR}/x")).status_code == 404
    assert mowl_api.qr_authorizations == []
    shown = (await actor.get("/api/connections")).json()
    assert shown["mowl_qr_enabled"] is False


async def test_the_switch_is_shown_on_the_connections_answer(qr_env: Env, make_actor: Any) -> None:
    actor = await make_actor(await qr_env.user(USER))
    assert (await actor.get("/api/connections")).json()["mowl_qr_enabled"] is True


async def test_a_start_needs_recent_authentication(
    qr_env: Env, make_actor: Any, mowl_api: FakeMowl
) -> None:
    actor = await make_actor(await qr_env.user(USER))
    await qr_env.run("UPDATE sessions SET reauth_at = now() - interval '11 minutes'")
    response = await actor.post(QR)
    assert (response.status_code, response.json()) == (
        403,
        {"detail": {"reason": "reauth_required"}},
    )
    assert mowl_api.qr_authorizations == []


async def test_another_user_or_session_never_sees_the_wait(
    qr_env: Env,
    make_actor: Any,
    image_host: FakeImageHost,
    broker: FakeBroker,
) -> None:
    owner_id = await qr_env.user(USER)
    actor = await make_actor(owner_id)
    other = await make_actor(await qr_env.user("other@example.com"))
    second_session = await make_actor(owner_id)
    ticket_id = (await actor.post(QR)).json()["ticket_id"]
    await broker.subscribed()
    for stranger in (other, second_session):
        for path in (f"{QR}/{ticket_id}", f"{QR}/{ticket_id}/image"):
            response = await stranger.get(path)
            assert (response.status_code, response.json()) == (
                404,
                {"detail": {"reason": "not_found"}},
            )
        assert (await stranger.call("DELETE", f"{QR}/{ticket_id}")).status_code == 204
    assert (await poll(actor, ticket_id, "waiting"))["status"] == "waiting"  # not cancelled
    broker.scan()
    await poll(actor, ticket_id, "connected")
    # Only the owner's row exists; the other user has none.
    assert await owned(qr_env, "SELECT count(*) FROM connections") == 1
    assert await owned(qr_env, "SELECT count(*) FROM connections", "other@example.com") == 0


async def test_a_cancel_ends_the_wait_and_stores_nothing(
    qr_env: Env, make_actor: Any, image_host: FakeImageHost, broker: FakeBroker
) -> None:
    actor = await make_actor(await qr_env.user(USER))
    ticket_id = (await actor.post(QR)).json()["ticket_id"]
    await broker.subscribed()
    assert (await actor.call("DELETE", f"{QR}/{ticket_id}")).status_code == 204
    assert (await poll(actor, ticket_id, "cancelled"))["connection"] is None
    broker.scan()
    await asyncio.sleep(0.1)
    assert (await poll(actor, ticket_id, "cancelled"))["status"] == "cancelled"
    assert await owned(qr_env, "SELECT count(*) FROM connections") == 0


@pytest.mark.parametrize(
    ("where", "code", "status", "reason"),
    [
        ("ticket", 503, 503, "upstream_unavailable"),
        ("ticket", 401, 502, "unknown"),
        ("image", 404, 502, "unknown"),
        ("image", 503, 503, "upstream_unavailable"),
    ],
)
async def test_a_failed_start_is_a_category_and_an_event(
    qr_env: Env,
    make_actor: Any,
    mowl_api: FakeMowl,
    image_host: FakeImageHost,
    broker: FakeBroker,
    where: str,
    code: int,
    status: int,
    reason: str,
) -> None:
    if where == "ticket":
        mowl_api.status["qr"] = code
    else:
        image_host.status = code
    actor = await make_actor(await qr_env.user(USER))
    response = await actor.post(QR)
    assert (response.status_code, response.json()) == (status, {"detail": {"reason": reason}})
    assert broker.opened == 0  # no broker connection without a ticket and its image
    assert await events(qr_env) == [
        ("connection_failed", {"provider": "mowl", "method": "qr", "category": reason})
    ]


async def test_starts_are_rate_limited_per_user(
    qr_env: Env, make_actor: Any, mowl_api: FakeMowl
) -> None:
    mowl_api.status["qr"] = 503  # each start fails fast, and still counts
    actor = await make_actor(await qr_env.user(USER))
    for _ in range(10):
        assert (await actor.post(QR)).status_code == 503
    refused = await actor.post(QR)
    assert refused.status_code == 429
    assert int(refused.headers["retry-after"]) > 0
    assert len(mowl_api.qr_authorizations) == 10  # the refused start never called MOWL


async def test_a_failed_wait_is_shown_with_its_category(
    qr_env: Env, make_actor: Any, image_host: FakeImageHost, broker: FakeBroker
) -> None:
    broker.connack = 5  # not authorized
    actor = await make_actor(await qr_env.user(USER))
    ticket_id = (await actor.post(QR)).json()["ticket_id"]
    shown = await poll(actor, ticket_id, "failed")
    assert (shown["reason"], shown["connection"]) == ("upstream_unavailable", None)
