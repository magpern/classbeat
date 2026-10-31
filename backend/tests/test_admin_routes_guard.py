"""The admin router shell (ADR 0011 section 6, #210): every ``/api/admin`` route has the gate
(the router guard), the gate's refusals and their audit rows, 503 ``admin_unavailable`` without
the maint credentials, and web refusing to start on a wrong maint role."""

import time
from collections.abc import AsyncIterator
from typing import Any

import pytest
from fastapi import APIRouter, Request
from sqlalchemy import Float, func, literal, select, text
from sqlalchemy.engine import Connection, make_url

from authtools import Env, running_env, session_cookie
from classbeat.admin import routes as admin_routes
from classbeat.admin.db import MaintRoleError, admin_session
from classbeat.admin.routes import AdminActor, admin_area_routes, admin_router, gate_problems
from classbeat.auth import ratelimit

PROBE = "/api/admin/probe"


def _probe_router() -> APIRouter:
    router = admin_router()

    @router.get("/probe")
    async def read_probe(request: Request, actor: AdminActor) -> dict[str, Any]:
        db = request.app.state.admin_db
        async with admin_session(db, actor, "admin.request",
                                 params={"method": "GET", "route": PROBE}) as session:  # fmt: skip
            users = await session.execute(select(func.count()).select_from(text("users")))
            return {"users": users.scalar_one()}

    @router.get("/users/{userId}")
    async def camel_probe(userId: str, actor: AdminActor) -> dict[str, str]:
        return {"user": userId}

    @router.post("/probe")
    async def write_probe(actor: AdminActor) -> dict[str, str]:
        return {"actor": str(actor.user_id)}

    return router


def _maint_settings(maint_database_url: str) -> dict[str, object]:
    url = make_url(maint_database_url)
    return {
        "maint_database_url": url.set(password=None).render_as_string(hide_password=False),
        "database_maint_password": url.password,
    }


@pytest.fixture(autouse=True)
def _probe_routes(monkeypatch: pytest.MonkeyPatch) -> None:
    """The admin router as a feature module would fill it, for the apps these tests build."""
    monkeypatch.setattr(admin_routes, "router", _probe_router())


@pytest.fixture
async def env(app_database_url: str, maint_database_url: str) -> AsyncIterator[Env]:
    """This module's app has the maint credentials (``make_actor`` signs in to it)."""
    async with running_env(app_database_url, **_maint_settings(maint_database_url)) as env:
        yield env


@pytest.fixture
async def bare_env(clean_auth: None, app_database_url: str) -> AsyncIterator[Env]:
    """An app without the maint credentials."""
    async with running_env(app_database_url) as env:
        yield env


def reason(response: Any) -> str:
    return response.json()["detail"]["reason"]


def rows(conn: Connection, user_id: object = None) -> list[tuple[str, str | None]]:
    """``(outcome, reason)`` of the audit rows of ``user_id`` (of everyone with ``None``)."""
    query = text(
        "SELECT outcome, reason FROM admin_audit "
        "WHERE CAST(:u AS uuid) IS NULL OR actor_id = CAST(:u AS uuid) ORDER BY created_at, id"
    )
    return [(r.outcome, r.reason) for r in conn.execute(query, {"u": user_id})]


async def test_every_admin_route_of_the_app_has_the_gate(env: Env) -> None:
    assert gate_problems(env.app.routes) == []
    # The enumeration reaches the included routers (it would pass vacuously otherwise).
    paths = {path for path, _ in admin_area_routes(env.app.routes)}
    assert {"/api/admin/whoami", PROBE} <= paths


def test_the_router_guard_sees_an_ungated_route_included_in_an_app() -> None:
    from fastapi import FastAPI

    bare = APIRouter(prefix="/admin")

    @bare.get("/users")
    async def users() -> None: ...

    outer = APIRouter(prefix="/api")
    outer.include_router(bare)
    app = FastAPI()
    app.include_router(outer)
    assert gate_problems(app.routes) == ["['GET'] /api/admin/users: no admin_gate"]


def test_the_router_guard_fails_for_a_route_without_the_gate() -> None:
    bare = APIRouter(prefix="/api/admin")

    @bare.get("/users")
    async def users() -> None: ...

    @bare.get("/whoami")
    async def whoami() -> None: ...

    problems = gate_problems(bare.routes)
    assert problems == ["['GET'] /api/admin/users: no admin_gate",
                        "['GET'] /api/admin/whoami: no require_admin"]  # fmt: skip
    assert gate_problems(_probe_router().routes) == []


async def test_an_admin_read_leaves_one_ok_row(
    env: Env, make_actor: Any, superuser: Connection
) -> None:
    user_id = await env.user("a@example.com", role="admin")
    actor = await make_actor(user_id, mfa=True)
    response = await actor.get(PROBE)
    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "no-store"
    assert rows(superuser, user_id) == [("ok", None)]


async def test_a_non_admin_gets_403_and_no_audit_row(
    env: Env, make_actor: Any, superuser: Connection
) -> None:
    actor = await make_actor(await env.user("plain@example.com"), mfa=True)
    for _ in range(3):
        response = await actor.get(PROBE)
        assert response.status_code == 403
        assert reason(response) == "forbidden"
    assert reason(await actor.post(PROBE)) == "forbidden"
    assert rows(superuser) == []


@pytest.fixture
def one_minute(env: Env, monkeypatch: pytest.MonkeyPatch) -> None:
    """Both per-minute counts held in one fixed window for the whole test (#284): the admin rate
    limit's window (``ratelimit._window_start``, the database's ``clock_timestamp()`` otherwise)
    and the refusal-row limiter's (``RefusalLimiter.clock``, ``time.monotonic`` otherwise). Both
    are fixed calendar windows by design (ADR 0003 section 9, ADR 0011 section 6), so requests
    that happen to cross a minute boundary would start a new count and the test would flake."""
    instant = float(int(time.time() // 60) * 60)

    def frozen_window(window: int) -> Any:
        return func.to_timestamp(literal(instant // window * window, Float))

    monkeypatch.setattr(ratelimit, "_window_start", frozen_window)
    env.app.state.admin_db.limiter.clock = lambda: instant


async def test_25_refused_calls_in_a_minute_leave_20_rows(
    env: Env, make_actor: Any, superuser: Connection, one_minute: None
) -> None:
    user_id = await env.user("a@example.com", role="admin")
    actor = await make_actor(user_id)  # no MFA
    for _ in range(25):
        response = await actor.get(PROBE)
        assert response.status_code == 403
        assert reason(response) == "mfa_required"
        assert response.headers["cache-control"] == "no-store"
    assert rows(superuser, user_id) == [("refused", "mfa_required")] * 20


async def test_a_write_needs_a_recent_stepup(
    env: Env, make_actor: Any, superuser: Connection
) -> None:
    user_id = await env.user("a@example.com", role="admin")
    actor = await make_actor(user_id, mfa=True)
    response = await actor.post(PROBE)
    assert response.status_code == 403
    assert reason(response) == "stepup_required"
    assert rows(superuser, user_id) == [("refused", "stepup_required")]
    await env.run("UPDATE sessions SET stepup_at = now()")
    response = await actor.post(PROBE)
    assert response.status_code == 200, response.text
    assert response.json() == {"actor": str(user_id)}


async def test_the_admin_rate_limit_refuses_with_an_audit_row(
    env: Env, make_actor: Any, superuser: Connection, one_minute: None
) -> None:
    """Threats: T93."""
    user_id = await env.user("a@example.com", role="admin")
    actor = await make_actor(user_id, mfa=True)
    statuses = [(await actor.get(PROBE)).status_code for _ in range(61)]
    assert statuses[:60] == [200] * 60
    assert statuses[60] == 429
    found = rows(superuser, user_id)
    assert found.count(("ok", None)) == 60
    assert found[-1] == ("refused", "rate_limited")


async def test_without_the_maint_secret_admin_routes_answer_503_and_the_rest_works(
    bare_env: Env, superuser: Connection
) -> None:
    env = bare_env
    assert env.app.state.admin_db is None
    user_id = await env.user("a@example.com", role="admin")
    cookie = session_cookie((await env.login(user_id, mfa=True)).token)
    response = await env.client.get(PROBE, headers=cookie)
    assert response.status_code == 503
    assert reason(response) == "admin_unavailable"
    assert (await env.client.get("/api/admin/whoami", headers=cookie)).status_code == 200
    assert (await env.client.get("/api/health")).status_code == 200
    plain = session_cookie((await env.login(await env.user("plain@example.com"))).token)
    assert (await env.client.get("/api/auth/session", headers=plain)).status_code == 200
    assert rows(superuser) == []


async def test_an_empty_maint_password_counts_as_none(
    app_database_url: str, maint_database_url: str
) -> None:
    settings = {**_maint_settings(maint_database_url), "database_maint_password": ""}
    async with running_env(app_database_url, **settings) as env:
        assert env.app.state.admin_db is None


@pytest.mark.parametrize("who", ["superuser", "app"])
async def test_web_refuses_to_start_on_a_wrong_maint_role(
    who: str, app_database_url: str, database_url: str, clean_auth: None
) -> None:
    url = make_url(database_url if who == "superuser" else app_database_url)
    settings = {
        "maint_database_url": url.set(password=None).render_as_string(hide_password=False),
        "database_maint_password": url.password,
    }
    with pytest.raises(MaintRoleError):
        async with running_env(app_database_url, **settings):
            pass


async def test_web_refuses_to_start_on_a_drifted_maint_role(
    app_database_url: str, maint_database_url: str, superuser: Connection
) -> None:
    superuser.execute(text("ALTER ROLE classbeat_maint SET work_mem = '8MB'"))
    try:
        with pytest.raises(MaintRoleError, match="role settings"):
            async with running_env(app_database_url, **_maint_settings(maint_database_url)):
                pass
    finally:
        superuser.execute(text("ALTER ROLE classbeat_maint RESET work_mem"))


def test_the_shared_router_is_gated() -> None:
    assert admin_routes.router.prefix == "/api/admin"
    assert gate_problems(admin_routes.router.routes) == []


async def test_a_route_template_outside_the_params_pattern_keeps_the_refusal(
    env: Env, make_actor: Any, superuser: Connection
) -> None:
    user_id = await env.user("a@example.com", role="admin")
    actor = await make_actor(user_id)  # no MFA: the gate refuses and audits
    response = await actor.get("/api/admin/users/abc")
    assert response.status_code == 403
    assert reason(response) == "mfa_required"
    params = superuser.execute(
        text("SELECT params FROM admin_audit WHERE actor_id = :u"), {"u": user_id}
    ).scalar_one()
    assert params == {"method": "GET", "route": "/api/admin"}
