"""The admin Model routes (ADR 0011 sections 5 and 6, ADR 0012 section 5; AD5 #223).

The gate on every route and the audit row of every call (written first, in the same transaction
as the write); ``set_route``'s refusals reach the admin as 409 with the reason and change nothing;
a stale route whose newer result failed is shown as refused by the worker; the maint role may read
results but never write one. Fakes only."""

from collections.abc import AsyncIterator
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Connection, make_url
from sqlalchemy.exc import DBAPIError

from adminviews import audit, reason, stepped_admin
from authtools import Env, running_env
from classbeat.admin.routes import admin_area_routes, gate_problems
from modeltools import OPUS, add_result, clear_gate, extend_catalog

MODELS = "/api/admin/models"
ROUTE = f"{MODELS}/routes"
SET = {"provider": "anthropic", "model": OPUS, "effort": "medium"}


@pytest.fixture
async def env(app_database_url: str, maint_database_url: str) -> AsyncIterator[Env]:
    url = make_url(maint_database_url)
    async with running_env(
        app_database_url,
        maint_database_url=url.set(password=None).render_as_string(hide_password=False),
        database_maint_password=url.password,
    ) as running:
        yield running


@pytest.fixture(autouse=True)
def clean_gate(superuser: Connection) -> Any:
    clear_gate(superuser)
    yield
    clear_gate(superuser)


def routes_in_db(conn: Connection) -> list[Any]:
    return list(conn.execute(text("SELECT route_key, stage_group, eval_result_id, set_by "
                                  "FROM model_routes ORDER BY 1, 2")))  # fmt: skip


def audit_rows(conn: Connection, actor_id: object) -> list[Any]:
    return list(
        conn.execute(
            text(
                "SELECT action, outcome, reason, target_kind, target_id, params FROM admin_audit "
                "WHERE actor_id = :u ORDER BY created_at, id"
            ),
            {"u": actor_id},
        )
    )


async def test_the_router_guard_covers_the_model_routes(env: Env) -> None:
    assert gate_problems(env.app.routes) == []
    paths = {path for path, _ in admin_area_routes(env.app.routes)}
    assert {MODELS, f"{ROUTE}/{{route_key}}/{{stage_group}}"} <= paths


async def test_non_admin_mfa_and_stepup_refusals_change_nothing(
    env: Env, make_actor: Any, superuser: Connection
) -> None:
    add_result(superuser)
    calls = [
        ("GET", MODELS, None),
        ("PUT", f"{ROUTE}/pro/design", SET),
        ("DELETE", f"{ROUTE}/pro/design", None),
    ]

    async def send(actor: Any, method: str, path: str, body: Any) -> Any:
        return await (actor.get(path) if method == "GET" else actor.call(method, path, body))

    plain = await make_actor(await env.user("plain@example.com"), mfa=True)
    await env.run("UPDATE sessions SET stepup_at = now()")
    for method, path, body in calls:
        response = await send(plain, method, path, body)
        assert (response.status_code, reason(response)) == (403, "forbidden"), path
    admin = await env.user("a@example.com", role="admin")
    no_stepup = await make_actor(admin, mfa=True)
    await env.run("UPDATE sessions SET stepup_at = NULL WHERE user_id = :u", u=admin)
    for method, path, body in calls[1:]:
        response = await send(no_stepup, method, path, body)
        assert (response.status_code, reason(response)) == (403, "stepup_required"), path
    assert routes_in_db(superuser) == []


async def test_setting_a_route_audits_first_and_the_route_stands_on_the_passing_result(
    env: Env, make_actor: Any, superuser: Connection
) -> None:
    actor = await stepped_admin(env, make_actor)
    result = add_result(superuser)
    response = await actor.call("PUT", f"{ROUTE}/pro/design", SET)
    assert response.status_code == 200, response.text
    assert response.json()["result"]["id"] == str(result)
    assert response.json()["stale"] == []
    [(key, group, stands_on, set_by)] = routes_in_db(superuser)
    assert (key, group, stands_on) == ("pro", "design", result)
    assert set_by == "a@example.com"  # the admin, from the session, never the body
    [row] = audit_rows(superuser, actor.user_id)
    assert (row.action, row.outcome, row.target_kind, row.target_id) == (
        "model.route_set", "ok", "model_route", "pro/design",
    )  # fmt: skip
    assert row.params["route_key"] == "pro" and row.params["model"] == OPUS
    shown = (await actor.get(MODELS)).json()
    assert [
        (r["route_key"], r["stage_group"], r["refused_by_worker"]) for r in shown["routes"]
    ] == [("pro", "design", False)]
    assert [p["route_key"] for p in shown["plans"]] == ["default"] * 3
    assert [r["id"] for r in shown["results"]] == [str(result)]


async def test_a_refused_route_is_409_with_its_reason_audited_and_changes_nothing(
    env: Env, make_actor: Any, superuser: Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    actor = await stepped_admin(env, make_actor)
    accepted = extend_catalog(monkeypatch)
    add_result(superuser, "anthropic", OPUS, "medium", "design", passed=False)
    add_result(superuser, "deepseek", accepted.deepseek.model, "medium", "design")
    cases = [
        (SET, "result_failed"),  # the newest result failed
        ({**SET, "model": "claude-nope-1"}, "not_in_catalog"),
        ({**SET, "effort": "ultra"}, "effort_not_in_catalog"),
        ({"provider": "openai", "model": "gpt-test", "effort": "medium"}, "data_policy"),
        (
            {"provider": "deepseek", "model": accepted.deepseek.model, "effort": "medium"},
            "deepseek_disabled",  # a passing result and an allowed entry, but the switch is off
        ),
        ({**SET, "model": "claude-sonnet-5-5"}, "no_result"),
    ]
    for body, expected in cases:
        response = await actor.call("PUT", f"{ROUTE}/pro/design", body)
        assert (response.status_code, reason(response)) == (409, expected), body
    assert routes_in_db(superuser) == []
    rows = audit_rows(superuser, actor.user_id)
    assert [(r.action, r.outcome, r.reason) for r in rows] == [
        ("model.route_set", "refused", expected) for _, expected in cases
    ]


async def test_a_set_that_replaces_a_route_is_refused_whole_when_the_result_is_stale(
    env: Env, make_actor: Any, superuser: Connection
) -> None:
    actor = await stepped_admin(env, make_actor)
    add_result(superuser)
    assert (await actor.call("PUT", f"{ROUTE}/pro/design", SET)).status_code == 200
    before = routes_in_db(superuser)
    add_result(superuser, passed=False)  # a newer failure: no passing result stands any more
    response = await actor.call("PUT", f"{ROUTE}/pro/design", SET)
    assert (response.status_code, reason(response)) == (409, "result_failed")
    assert routes_in_db(superuser) == before  # the old route is untouched by the refusal


async def test_a_route_with_a_newer_failure_is_shown_as_refused_by_the_worker(
    env: Env, make_actor: Any, superuser: Connection
) -> None:
    actor = await stepped_admin(env, make_actor)
    add_result(superuser, age_minutes=10)
    assert (await actor.call("PUT", f"{ROUTE}/pro/design", SET)).status_code == 200
    add_result(superuser, passed=False)
    [route] = (await actor.get(MODELS)).json()["routes"]
    assert route["stale"] == ["newer_failure"]
    assert route["refused_by_worker"] is True


async def test_clearing_a_route_is_audited_and_a_missing_one_is_404(
    env: Env, make_actor: Any, superuser: Connection
) -> None:
    actor = await stepped_admin(env, make_actor)
    add_result(superuser)
    assert (await actor.call("PUT", f"{ROUTE}/pro/design", SET)).status_code == 200
    assert (await actor.call("DELETE", f"{ROUTE}/pro/design", None)).status_code == 204
    assert routes_in_db(superuser) == []
    again = await actor.call("DELETE", f"{ROUTE}/pro/design", None)
    assert (again.status_code, reason(again)) == (404, "no_route")
    assert [(r.action, r.outcome, r.reason) for r in audit_rows(superuser, actor.user_id)] == [
        ("model.route_set", "ok", None),
        ("model.route_clear", "ok", None),
        ("model.route_clear", "refused", "no_route"),
    ]


async def test_malformed_input_is_422_and_never_reaches_the_gate(
    env: Env, make_actor: Any, superuser: Connection
) -> None:
    actor = await stepped_admin(env, make_actor)
    add_result(superuser)
    for path, body in [
        (f"{ROUTE}/Pro/design", SET),  # a route key is lower case
        (f"{ROUTE}/pro/everything", SET),  # an unknown stage group
        (f"{ROUTE}/pro/design", {**SET, "model": "x" * 101}),
        (f"{ROUTE}/pro/design", {**SET, "effort": "Medium; DROP"}),
        (f"{ROUTE}/pro/design", {**SET, "set_by": "someone else"}),  # extra fields are refused
        (f"{ROUTE}/pro/design", {"provider": "anthropic"}),
    ]:
        response = await actor.call("PUT", path, body)
        assert response.status_code == 422, (path, body)
    assert routes_in_db(superuser) == []


async def test_maint_reads_results_but_cannot_write_them(
    maint_database_url: str, superuser: Connection
) -> None:
    from sqlalchemy import create_engine

    add_result(superuser)
    engine = create_engine(maint_database_url, isolation_level="AUTOCOMMIT")
    try:
        with engine.connect() as maint:
            assert maint.execute(text("SELECT count(*) FROM model_eval_results")).scalar_one() == 1
            for statement in (
                "UPDATE model_eval_results SET passed = passed",
                "DELETE FROM model_eval_results",
                "INSERT INTO model_eval_results (id) VALUES (gen_random_uuid())",
            ):
                with pytest.raises(DBAPIError, match="permission denied|read-only"):
                    maint.execute(text(statement))
    finally:
        engine.dispose()
