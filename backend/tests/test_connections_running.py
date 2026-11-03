"""``GET /api/connections`` carries the effective ``running_enabled`` (ADR 0018 section 5, M8 U1):
the environment's switch and the admin flag, stricter wins. Signed-in users only."""

import json
from collections.abc import AsyncIterator, Iterator
from typing import Any

import pytest
from sqlalchemy import Connection, text

from authtools import Env, running_env


@pytest.fixture
async def env(clean_auth: None, app_database_url: str) -> AsyncIterator[Env]:
    async with running_env(app_database_url, running_enabled=True) as running:
        yield running


@pytest.fixture(autouse=True)
def clean_flag(superuser: Connection) -> Iterator[None]:
    superuser.execute(text("DELETE FROM app_settings WHERE key = 'running_enabled'"))
    superuser.commit()
    yield
    superuser.execute(text("DELETE FROM app_settings WHERE key = 'running_enabled'"))
    superuser.commit()


def store(conn: Connection, value: str) -> None:
    conn.execute(
        text(
            "INSERT INTO app_settings (key, value) VALUES ('running_enabled', CAST(:v AS jsonb)) "
            "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value"
        ),
        {"v": json.dumps(value)},
    )
    conn.commit()


async def shown(actor: Any) -> Any:
    response = await actor.get("/api/connections")
    assert response.status_code == 200
    return response.json()["running_enabled"]


async def test_environment_off_is_off(env: Env, make_actor: Any, superuser: Connection) -> None:
    actor = await make_actor(await env.user("user@example.com"))
    env.app.state.settings = env.settings.model_copy(update={"running_enabled": False})
    assert await shown(actor) is False
    store(superuser, "on")  # the admin flag can only tighten
    assert await shown(actor) is False


async def test_environment_on_follows_the_admin_flag(
    env: Env, make_actor: Any, superuser: Connection
) -> None:
    actor = await make_actor(await env.user("user@example.com"))
    assert await shown(actor) is True
    store(superuser, "off")
    assert await shown(actor) is False
    store(superuser, "on")
    assert await shown(actor) is True


async def test_not_signed_in_is_refused(env: Env) -> None:
    assert (await env.client.get("/api/connections")).status_code == 401
