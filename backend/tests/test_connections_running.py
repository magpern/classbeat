"""``GET /api/connections`` carries the effective ``running_enabled`` (ADR 0018 section 5, M8 U1,
and its amendment of 2026-11-04, #509): on only when the environment's switch is on, the stored
value is exactly ``on`` and every plan's ``route_key`` has a ``design`` route covering running,
read on every request. Signed-in users only. Fakes only: no model, no network."""

import json
from collections.abc import AsyncIterator, Iterator
from typing import Any

import pytest
from sqlalchemy import Connection, text

from authtools import Env, make_settings, running_env
from classbeat.design import sport
from modeltools import add_result, clear_gate


@pytest.fixture
async def env(clean_auth: None, app_database_url: str) -> AsyncIterator[Env]:
    async with running_env(app_database_url, running_enabled=True) as running:
        yield running
    sport.configure(None)


@pytest.fixture(autouse=True)
def clean_flag(superuser: Connection) -> Iterator[None]:
    superuser.execute(text("DELETE FROM app_settings WHERE key = 'running_enabled'"))
    clear_gate(superuser)
    yield
    superuser.execute(text("DELETE FROM app_settings WHERE key = 'running_enabled'"))
    superuser.execute(text("DELETE FROM plans WHERE id = 'later'"))
    clear_gate(superuser)


def store(conn: Connection, value: Any, *, raw: str | None = None) -> None:
    conn.execute(
        text(
            "INSERT INTO app_settings (key, value) VALUES ('running_enabled', CAST(:v AS jsonb)) "
            "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value"
        ),
        {"v": raw if raw is not None else json.dumps(value)},
    )


def plan_keys(conn: Connection) -> list[str]:
    return list(conn.execute(text("SELECT DISTINCT route_key FROM plans ORDER BY 1")).scalars())


def route(conn: Connection, *keys: str) -> None:
    """A passing design-group result on the running suite and a ``design`` route at it for each
    of ``keys`` (setup, as the superuser)."""
    result = add_result(conn)
    for key in keys:
        conn.execute(
            text(
                "INSERT INTO model_routes (route_key, stage_group, provider, model, effort, "
                "eval_result_id, set_at, set_by, gate_passed) SELECT :k, 'design', provider, "
                "model, effort, id, now(), 'setup', true FROM model_eval_results WHERE id = :r"
            ),
            {"k": key, "r": result},
        )


async def shown(actor: Any) -> Any:
    response = await actor.get("/api/connections")
    assert response.status_code == 200
    return response.json()["running_enabled"]


async def test_environment_off_is_off(env: Env, make_actor: Any, superuser: Connection) -> None:
    actor = await make_actor(await env.user("user@example.com"))
    sport.configure(sport.SportGate(running_enabled=False, fake_covers=True))
    store(superuser, "on")  # the admin flag can only tighten
    route(superuser, *plan_keys(superuser))
    assert await shown(actor) is False


@pytest.mark.parametrize("case", ["nothing_stored", "deleted", "invalid", "stored_off"])
async def test_only_a_stored_on_switches_it_on(
    env: Env, make_actor: Any, superuser: Connection, case: str
) -> None:
    """#509: the environment on with nothing stored, the admin's DELETE (the JSON ``null``), an
    invalid or an ``off`` value are all off, even with every plan covered."""
    actor = await make_actor(await env.user("user@example.com"))
    sport.configure(sport.SportGate(running_enabled=True, fake_covers=True))
    route(superuser, *plan_keys(superuser))
    if case == "deleted":
        store(superuser, None, raw="null")
    elif case == "invalid":
        store(superuser, "yes")
    elif case == "stored_off":
        store(superuser, "off")
    assert await shown(actor) is False
    store(superuser, "on")
    assert await shown(actor) is True


async def test_a_stored_on_needs_every_plans_route_to_cover_running_on_every_read(
    env: Env, make_actor: Any, superuser: Connection
) -> None:
    """#509: without the web's own ``fake``, the effective flag needs a covering ``design`` route
    for every plan's ``route_key``, read on each request: a route cleared later or a plan added
    later turns it off with nothing stored."""
    actor = await make_actor(await env.user("user@example.com"))
    sport.configure(sport.SportGate(running_enabled=True, fake_covers=False))
    store(superuser, "on")
    assert await shown(actor) is False  # no route at all: the bootstrap covers nothing
    keys = plan_keys(superuser)
    route(superuser, *keys[1:])
    assert await shown(actor) is False  # one plan has none
    route(superuser, keys[0])
    assert await shown(actor) is True
    superuser.execute(text("DELETE FROM model_routes WHERE route_key = :k"), {"k": keys[-1]})
    assert await shown(actor) is False  # a route cleared later
    route(superuser, keys[-1])
    assert await shown(actor) is True
    superuser.execute(
        text(
            "INSERT INTO plans (id, name, rank, classes_per_month, revisions_per_class, "
            "concurrent_jobs, monthly_cost_micros, route_key) "
            "VALUES ('later', 'Later', 99, 3, 3, 1, 10000000, 'later')"
        )
    )
    assert await shown(actor) is False  # a plan added later, with no route
    route(superuser, "later")
    assert await shown(actor) is True
    # A newer failed result for the same model makes the route stale: it covers nothing.
    add_result(superuser, passed=False, age_minutes=-1)
    assert await shown(actor) is False


@pytest.mark.parametrize(
    ("provider", "environment", "expected"),
    [("fake", "test", True), ("fake", "dev", True), ("fake", "prod", False)]
    + [("anthropic", e, False) for e in ("test", "dev", "prod")],
)
def test_the_webs_fake_covers_only_outside_prod(
    provider: str, environment: str, expected: bool
) -> None:
    """The web's own ``fake`` provider counts as covering (the e2e stack); in prod it never does,
    so a production web needs a recorded route for every plan."""
    settings = make_settings(
        "postgresql+psycopg://u:p@127.0.0.1/x",
        running_enabled=True,
        model_provider=provider,
        environment=environment,
    )
    assert sport.SportGate.from_settings(settings) == sport.SportGate(
        running_enabled=True, fake_covers=expected
    )


async def test_the_webs_fake_turns_it_on_with_a_stored_on_and_no_route(
    clean_auth: None, app_database_url: str, make_actor: Any, superuser: Connection
) -> None:
    async with running_env(app_database_url, running_enabled=True, model_provider="fake") as env:
        try:
            actor = await make_actor(await env.user("user@example.com"))
            assert await shown(actor) is False  # nothing stored
            store(superuser, "on")
            assert await shown(actor) is True
        finally:
            sport.configure(None)


async def test_not_signed_in_is_refused(env: Env) -> None:
    assert (await env.client.get("/api/connections")).status_code == 401
