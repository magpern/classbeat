"""The admin Settings, flags and plan limits (ADR 0011 sections 4, 6 and 7; AD4 #222).

The gate on every route and the audit row of every call; a flag write goes through
``classbeat.flags`` validation (the database tightens the environment, never loosens it, and a
value it refuses is 422 and a ``refused`` audit row that changes nothing); a plan limit edit
stays under the environment's bounds and leaves held credits alone; audit ``params`` never hold
free text. Fakes only."""

import json
import uuid
from collections.abc import AsyncIterator
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Connection, make_url

from adminviews import audit, reason, stepped_admin
from authtools import Env, running_env
from classbeat.admin.routes import admin_area_routes, gate_problems
from flows import Browser
from test_plans_routes import plant_ledger

SETTINGS = "/api/admin/settings"
PLANS = "/api/admin/plans"
PUBLIC = "/api/settings/public"
CLASSES_MAX = 50
COST_MAX = 120_000_000
TRIAL_DAYS_MAX = 14


@pytest.fixture
async def env(app_database_url: str, maint_database_url: str) -> AsyncIterator[Env]:
    """The app with the admin area, an environment of ``signup_mode=open`` and small bounds."""
    url = make_url(maint_database_url)
    async with running_env(
        app_database_url,
        maint_database_url=url.set(password=None).render_as_string(hide_password=False),
        database_maint_password=url.password,
        signup_mode="open",
        song_picking=True,
        classes_per_month_max=CLASSES_MAX,
        monthly_cost_micros_max=COST_MAX,
        trial_days_max=TRIAL_DAYS_MAX,
    ) as running:
        yield running


@pytest.fixture(autouse=True)
def clean_settings(superuser: Connection) -> Any:
    """No ``app_settings`` row before or after, and the seeded plans as migration 0007 made them."""
    reset(superuser)
    yield
    reset(superuser)


def reset(conn: Connection) -> None:
    conn.execute(text("DELETE FROM app_settings"))
    conn.execute(
        text(
            "UPDATE plans SET classes_per_month = v.c, revisions_per_class = v.r, "
            "concurrent_jobs = v.j, monthly_cost_micros = v.m, updated_by = NULL "
            "FROM (VALUES ('free', 3, 3, 1, 10000000), ('instructor', 30, 10, 2, 60000000), "
            "('pro', 100, 25, 3, 200000000)) AS v (id, c, r, j, m) WHERE plans.id = v.id"
        )
    )


def store(conn: Connection, key: str, value: Any) -> None:
    conn.execute(
        text(
            "INSERT INTO app_settings (key, value) VALUES (:k, CAST(:v AS jsonb)) "
            "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value"
        ),
        {"k": key, "v": json.dumps(value)},
    )


def stored(conn: Connection, key: str) -> Any:
    return conn.execute(text("SELECT value FROM app_settings WHERE key = :k"), {"k": key}).scalar()


def rows_of(conn: Connection, actor_id: object) -> list[Any]:
    return list(
        conn.execute(
            text(
                "SELECT action, outcome, reason, target_kind, target_id, params "
                "FROM admin_audit WHERE actor_id = :u ORDER BY created_at, id"
            ),
            {"u": actor_id},
        )
    )


def given(params: dict[str, Any]) -> dict[str, Any]:
    """The params that say something: the audit model dumps every field, unset ones as null."""
    return {k: v for k, v in params.items() if v is not None}


def routes() -> list[tuple[str, str, Any]]:
    """Every route of this module as (method, path, body)."""
    return [
        ("GET", SETTINGS, None),
        ("PUT", f"{SETTINGS}/banner", {"value": {"text": "x", "level": "info"}}),
        ("DELETE", f"{SETTINGS}/banner", None),
        ("PATCH", f"{PLANS}/free", {"classes_per_month": 4}),
    ]


async def send(actor: Any, method: str, path: str, body: Any = None) -> Any:
    return await (actor.get(path) if method == "GET" else actor.call(method, path, body))


async def put(actor: Any, key: str, value: Any) -> Any:
    return await actor.call("PUT", f"{SETTINGS}/{key}", {"value": value})


def flag(response: Any, key: str) -> Any:
    (found,) = [f for f in response.json()["flags"] if f["key"] == key]
    return found


# --- the gate ---------------------------------------------------------------------------------


async def test_the_router_guard_covers_the_settings_routes(env: Env) -> None:
    assert gate_problems(env.app.routes) == []
    paths = {path for path, _ in admin_area_routes(env.app.routes)}
    assert {SETTINGS, f"{SETTINGS}/{{key}}", f"{PLANS}/{{plan_id}}"} <= paths


async def test_a_non_admin_gets_403_on_every_route_and_no_audit_row(
    env: Env, make_actor: Any, superuser: Connection
) -> None:
    plain = await env.user("plain@example.com")
    actor = await make_actor(plain, mfa=True)
    await env.run("UPDATE sessions SET stepup_at = now()")
    for method, path, body in routes():
        response = await send(actor, method, path, body)
        assert (response.status_code, reason(response)) == (403, "forbidden"), path
    assert superuser.execute(text("SELECT count(*) FROM admin_audit")).scalar_one() == 0
    assert superuser.execute(text("SELECT count(*) FROM app_settings")).scalar_one() == 0


async def test_mfa_and_stepup_refusals_are_audited_and_change_nothing(
    env: Env, make_actor: Any, superuser: Connection
) -> None:
    admin = await env.user("a@example.com", role="admin")
    no_mfa = await make_actor(admin)
    for method, path, body in routes():
        response = await send(no_mfa, method, path, body)
        assert (response.status_code, reason(response)) == (403, "mfa_required"), path
    no_stepup = await make_actor(admin, mfa=True)
    for method, path, body in routes()[1:]:
        response = await send(no_stepup, method, path, body)
        assert (response.status_code, reason(response)) == (403, "stepup_required"), path
    assert {r[:3] for r in audit(superuser, admin)} == {
        ("admin.request", "refused", "mfa_required"),
        ("admin.request", "refused", "stepup_required"),
    }
    assert superuser.execute(text("SELECT count(*) FROM app_settings")).scalar_one() == 0
    assert (
        superuser.execute(
            text("SELECT classes_per_month FROM plans WHERE id = 'free'")
        ).scalar_one()
        == 3
    )


# --- reading ----------------------------------------------------------------------------------


async def test_the_read_shows_each_flag_with_its_effective_value_and_source(
    env: Env, make_actor: Any, superuser: Connection
) -> None:
    actor = await stepped_admin(env, make_actor)
    body = (await actor.get(SETTINGS)).json()
    assert [f["key"] for f in body["flags"]] == ["signup_mode", "song_picking", "trial", "banner"]
    signup = flag(await actor.get(SETTINGS), "signup_mode")
    assert (signup["effective"], signup["source"], signup["environment"]) == (
        "open", "environment", "open",
    )  # fmt: skip
    store(superuser, "signup_mode", "closed")
    store(superuser, "song_picking", "on")
    store(superuser, "banner", {"text": "Back at nine", "level": "warning"})
    store(superuser, "trial", {"plan": "pro", "days": 99})
    view = await actor.get(SETTINGS)
    signup = flag(view, "signup_mode")
    assert (signup["effective"], signup["source"], signup["stored_valid"]) == (
        "closed", "database", True,
    )  # fmt: skip
    assert flag(view, "song_picking")["effective"] == "on"
    assert flag(view, "banner")["effective"] == {"text": "Back at nine", "level": "warning"}
    # The trial as it is used: clamped to the environment's days and to the Instructor plan.
    trial = flag(view, "trial")
    assert trial["effective"] == {"plan": "instructor", "days": TRIAL_DAYS_MAX}
    assert trial["stored"] == {"plan": "pro", "days": 99}
    assert [p["id"] for p in view.json()["plans"]] == ["free", "instructor", "pro"]
    assert view.json()["bounds"] == {
        "classes_per_month": CLASSES_MAX,
        "revisions_per_class": 25,
        "concurrent_jobs": 2,
        "monthly_cost_micros": COST_MAX,
        "trial_days": TRIAL_DAYS_MAX,
    }
    read_only = view.json()["read_only"]
    assert (read_only["billing_enabled"], read_only["swedebeat_enabled"]) == (False, False)
    assert read_only["agent_job_cost_micros"] > 0
    assert ("setting.read", "ok", None) in audit(superuser, actor.user_id)


async def test_an_invalid_stored_value_reads_as_invalid_with_the_stricter_default(
    env: Env, make_actor: Any, superuser: Connection
) -> None:
    actor = await stepped_admin(env, make_actor)
    store(superuser, "signup_mode", "open")  # not a storable value
    store(superuser, "song_picking", "maybe")
    store(superuser, "banner", {"text": "x" * 400, "level": "info"})
    view = await actor.get(SETTINGS)
    signup = flag(view, "signup_mode")
    assert (signup["stored_valid"], signup["effective"]) == (False, "invite")
    song = flag(view, "song_picking")
    assert (song["stored_valid"], song["effective"]) == (False, "granted")
    banner = flag(view, "banner")
    assert (banner["stored_valid"], banner["effective"]) == (False, None)


# --- flag writes ------------------------------------------------------------------------------


async def test_a_flag_write_is_stored_audited_and_takes_effect(
    env: Env, make_actor: Any, superuser: Connection
) -> None:
    actor = await stepped_admin(env, make_actor)
    response = await put(actor, "signup_mode", "closed")
    assert response.status_code == 200
    assert (response.json()["effective"], response.json()["source"]) == ("closed", "database")
    assert response.json()["updated_by"]["id"] == str(actor.user_id)
    assert stored(superuser, "signup_mode") == "closed"
    # The public endpoint (FL1) reads the same row.
    assert (await Browser(env).get(PUBLIC)).json()["signup_mode"] == "closed"
    assert (await put(actor, "song_picking", "off")).status_code == 200
    assert stored(superuser, "song_picking") == "off"
    assert (await put(actor, "trial", {"plan": "instructor", "days": 7})).status_code == 200
    assert stored(superuser, "trial") == {"plan": "instructor", "days": 7}
    again = await put(actor, "signup_mode", "invite")  # an overwrite
    assert (again.status_code, stored(superuser, "signup_mode")) == (200, "invite")
    ok = [r for r in rows_of(superuser, actor.user_id) if r.action == "setting.set"]
    assert [r.outcome for r in ok] == ["ok"] * 4
    assert (ok[0].target_kind, ok[0].target_id) == ("setting", "signup_mode")
    assert given(ok[0].params) == {"key": "signup_mode", "value": "closed"}
    assert given(ok[2].params) == {"key": "trial", "plan": "instructor", "days": 7}


async def test_the_banner_is_stored_as_text_and_its_text_is_not_in_the_audit_row(
    env: Env, make_actor: Any, superuser: Connection
) -> None:
    actor = await stepped_admin(env, make_actor)
    markup = "<b>Maintenance</b> tonight <img src=x onerror=alert(1)>"
    response = await put(actor, "banner", {"text": markup, "level": "warning"})
    assert response.status_code == 200
    shown = (await Browser(env).get(PUBLIC)).json()["banner"]
    assert shown == {"text": markup, "level": "warning"}  # data: the page renders it as text
    [row] = [r for r in rows_of(superuser, actor.user_id) if r.action == "setting.set"]
    assert given(row.params) == {"key": "banner", "level": "warning", "text_length": len(markup)}
    dump = superuser.execute(text("SELECT string_agg(params::text, ' ') FROM admin_audit")).scalar()
    assert "Maintenance" not in (dump or "")
    assert (await put(actor, "banner", {"text": "x" * 280, "level": "info"})).status_code == 200


@pytest.mark.parametrize(
    ("key", "value", "why"),
    [
        ("signup_mode", "open", "environment_only"),
        ("signup_mode", "everyone", "invalid_value"),
        ("signup_mode", 3, "invalid_value"),
        ("signup_mode", None, "invalid_value"),
        ("song_picking", "yes", "invalid_value"),
        ("trial", {"plan": "instructor", "days": TRIAL_DAYS_MAX + 1}, "above_environment_bound"),
        ("trial", {"plan": "pro", "days": 7}, "plan_above_instructor"),
        ("trial", {"plan": "nosuchplan", "days": 7}, "unknown_plan"),
        ("trial", {"plan": "instructor", "days": -1}, "invalid_value"),
        ("trial", {"plan": "instructor", "days": 1.5}, "invalid_value"),
        ("trial", {"plan": "instructor", "days": True}, "invalid_value"),
        ("trial", {"plan": "instructor"}, "invalid_value"),
        ("trial", {"plan": "instructor", "days": 5, "extra": 1}, "invalid_value"),
        ("trial", {"plan": "../x", "days": 5}, "invalid_value"),
        ("banner", {"text": "x" * 281, "level": "info"}, "invalid_value"),
        ("banner", {"text": "hi", "level": "danger"}, "invalid_value"),
        ("banner", {"text": "   ", "level": "info"}, "invalid_value"),
        ("banner", {"text": "bell\x07", "level": "info"}, "invalid_value"),
        ("banner", {"text": "hi"}, "invalid_value"),
        ("banner", "just text", "invalid_value"),
        ("billing", True, "unknown_key"),
        ("billing_enabled", True, "unknown_key"),
        ("swedebeat", "on", "unknown_key"),
    ],
)
async def test_a_value_the_environment_or_the_schema_forbids_is_422_and_audited_refused(
    env: Env, make_actor: Any, superuser: Connection, key: str, value: Any, why: str
) -> None:
    actor = await stepped_admin(env, make_actor)
    store(superuser, "banner", {"text": "kept", "level": "info"})  # nothing may change it
    before = superuser.execute(text("SELECT key, value FROM app_settings ORDER BY key")).all()
    response = await put(actor, key, value)
    assert (response.status_code, reason(response)) == (422, why)
    assert (
        superuser.execute(text("SELECT key, value FROM app_settings ORDER BY key")).all() == before
    )
    [row] = [r for r in rows_of(superuser, actor.user_id) if r.action == "setting.set"]
    assert (row.outcome, row.reason, row.target_id) == ("refused", why, key)
    # A refused value is not logged. A trial the plans table refuses for its rank names its plan
    # (a real plan code); one for an unknown plan records only the reason.
    expected = {"key": key}
    if why == "plan_above_instructor":
        expected |= {"plan": value["plan"], "days": value["days"]}
    assert given(row.params) == expected


async def test_a_flag_write_with_extra_fields_is_a_schema_error(
    env: Env, make_actor: Any, superuser: Connection
) -> None:
    actor = await stepped_admin(env, make_actor)
    response = await actor.call("PUT", f"{SETTINGS}/banner", {"value": "x", "force": True})
    assert response.status_code == 422
    assert stored(superuser, "banner") is None


async def test_clearing_a_flag_returns_it_to_the_environment(
    env: Env, make_actor: Any, superuser: Connection
) -> None:
    actor = await stepped_admin(env, make_actor)
    await put(actor, "signup_mode", "closed")
    cleared = await actor.call("DELETE", f"{SETTINGS}/signup_mode", None)
    assert cleared.status_code == 200
    assert (cleared.json()["effective"], cleared.json()["source"]) == ("open", "environment")
    assert stored(superuser, "signup_mode") is None  # the JSON null: no value, as no row
    nothing = await actor.call("DELETE", f"{SETTINGS}/signup_mode", None)
    assert (nothing.status_code, reason(nothing)) == (404, "not_found")
    unknown = await actor.call("DELETE", f"{SETTINGS}/billing", None)
    assert (unknown.status_code, reason(unknown)) == (422, "unknown_key")
    outcomes = [(r.action, r.outcome, r.reason) for r in rows_of(superuser, actor.user_id)]
    assert outcomes == [
        ("setting.set", "ok", None),
        ("setting.clear", "ok", None),
        ("setting.clear", "refused", "not_found"),
        ("setting.clear", "refused", "unknown_key"),
    ]


# --- plan limits ------------------------------------------------------------------------------


def plan_row(conn: Connection, plan_id: str) -> Any:
    return conn.execute(
        text(
            "SELECT classes_per_month, revisions_per_class, concurrent_jobs, monthly_cost_micros, "
            "updated_by FROM plans WHERE id = :p"
        ),
        {"p": plan_id},
    ).one()


async def test_a_plan_edit_is_stored_audited_and_leaves_held_credits_alone(
    env: Env, make_actor: Any, superuser: Connection
) -> None:
    actor = await stepped_admin(env, make_actor)
    holder = await env.user("holder@example.com")
    for state in ("reserved", "committed"):
        plant_ledger(superuser, holder, state)
    held = superuser.execute(
        text("SELECT id, state, plan_id FROM credit_ledger WHERE owner_id = :o ORDER BY id"),
        {"o": holder},
    ).all()
    response = await actor.call(
        "PATCH", f"{PLANS}/free", {"classes_per_month": 1, "monthly_cost_micros": 5_000_000}
    )
    assert response.status_code == 200
    body = response.json()
    assert (body["id"], body["classes_per_month"], body["monthly_cost_micros"]) == (
        "free", 1, 5_000_000,
    )  # fmt: skip
    assert (body["revisions_per_class"], body["concurrent_jobs"]) == (3, 1)  # untouched
    row = plan_row(superuser, "free")
    assert (row.classes_per_month, row.monthly_cost_micros, row.updated_by) == (
        1, 5_000_000, actor.user_id,
    )  # fmt: skip
    # The held credits are what they were; only the next reservation reads the new limit.
    assert (
        superuser.execute(
            text("SELECT id, state, plan_id FROM credit_ledger WHERE owner_id = :o ORDER BY id"),
            {"o": holder},
        ).all()
        == held
    )
    [audited] = [r for r in rows_of(superuser, actor.user_id) if r.action == "plan.update"]
    assert (audited.outcome, audited.target_kind, audited.target_id) == ("ok", "plan", "free")
    assert given(audited.params) == {"classes_per_month": 1, "monthly_cost_micros": 5_000_000}
    view = await actor.get(SETTINGS)
    free = next(p for p in view.json()["plans"] if p["id"] == "free")
    assert free["classes_per_month"] == 1


@pytest.mark.parametrize(
    ("body", "why"),
    [
        ({"classes_per_month": CLASSES_MAX + 1}, "above_environment_bound"),
        ({"monthly_cost_micros": COST_MAX + 1}, "above_environment_bound"),
        ({"concurrent_jobs": 3}, "above_environment_bound"),  # CLASSBEAT_JOBS_PER_USER is 2
        ({"revisions_per_class": 26}, "above_environment_bound"),  # the setting's 25
        ({"classes_per_month": 10_001}, "out_of_range"),
        ({"classes_per_month": -1}, "out_of_range"),
        ({"concurrent_jobs": 0}, "out_of_range"),
        ({"revisions_per_class": 201}, "out_of_range"),
        ({"monthly_cost_micros": 10_000_000_001}, "out_of_range"),
        ({}, "nothing_to_change"),
        ({"classes_per_month": 5, "monthly_cost_micros": COST_MAX + 1}, "above_environment_bound"),
    ],
)
async def test_a_limit_the_table_or_the_environment_forbids_is_422_and_changes_nothing(
    env: Env, make_actor: Any, superuser: Connection, body: dict[str, int], why: str
) -> None:
    actor = await stepped_admin(env, make_actor)
    before = plan_row(superuser, "pro")
    response = await actor.call("PATCH", f"{PLANS}/pro", body)
    assert (response.status_code, reason(response)) == (422, why)
    assert plan_row(superuser, "pro") == before
    [row] = [r for r in rows_of(superuser, actor.user_id) if r.action == "plan.update"]
    assert (row.outcome, row.reason, row.target_id) == ("refused", why, "pro")


async def test_a_plan_edit_at_the_bounds_is_accepted(
    env: Env, make_actor: Any, superuser: Connection
) -> None:
    actor = await stepped_admin(env, make_actor)
    edit = {"classes_per_month": CLASSES_MAX, "monthly_cost_micros": COST_MAX, "concurrent_jobs": 2}
    assert (await actor.call("PATCH", f"{PLANS}/pro", edit)).status_code == 200
    row = plan_row(superuser, "pro")
    assert (row.classes_per_month, row.monthly_cost_micros, row.concurrent_jobs) == (
        CLASSES_MAX, COST_MAX, 2,
    )  # fmt: skip


async def test_an_unknown_plan_is_404_and_a_bad_body_is_a_schema_error(
    env: Env, make_actor: Any, superuser: Connection
) -> None:
    actor = await stepped_admin(env, make_actor)
    missing = await actor.call("PATCH", f"{PLANS}/nosuch", {"classes_per_month": 1})
    assert (missing.status_code, reason(missing)) == (404, "not_found")
    [row] = [r for r in rows_of(superuser, actor.user_id) if r.action == "plan.update"]
    assert (row.outcome, row.reason) == ("refused", "not_found")
    for bad in (
        {"name": "Gold"},
        {"features": ["song_picking"]},
        {"rank": 1},
        {"classes_per_month": "3"},
    ):
        response = await actor.call("PATCH", f"{PLANS}/free", bad)
        assert response.status_code == 422, bad
    assert plan_row(superuser, "free").classes_per_month == 3  # only the four limits are editable


async def test_the_plans_other_columns_are_out_of_the_maint_roles_reach(
    env: Env, make_actor: Any, superuser: Connection
) -> None:
    """Defence in depth: even a handler bug could not edit ``name``, ``rank`` or ``features``."""
    actor = await stepped_admin(env, make_actor)
    assert actor is not None
    privileges = (
        superuser.execute(
            text(
                "SELECT column_name FROM information_schema.column_privileges "
                "WHERE table_name = 'plans' AND grantee = 'classbeat_maint' "
                "AND privilege_type = 'UPDATE' ORDER BY column_name"
            )
        )
        .scalars()
        .all()
    )
    assert privileges == [
        "classes_per_month", "concurrent_jobs", "monthly_cost_micros",
        "revisions_per_class", "updated_at", "updated_by",
    ]  # fmt: skip


async def test_a_refused_write_leaves_the_audit_log_free_of_the_rejected_values(
    env: Env, make_actor: Any, superuser: Connection
) -> None:
    actor = await stepped_admin(env, make_actor)
    secret = "do-not-log-this-text"
    await put(actor, "banner", {"text": secret + "\x07", "level": "info"})
    await put(actor, "trial", {"plan": secret, "days": 1})
    await actor.call("PATCH", f"{PLANS}/{uuid.uuid4().hex[:8]}", {"classes_per_month": 1})
    dump = superuser.execute(text("SELECT string_agg(params::text, ' ') FROM admin_audit")).scalar()
    assert secret not in (dump or "")


async def test_a_trial_of_exactly_the_environments_days_is_accepted(
    env: Env, make_actor: Any, superuser: Connection
) -> None:
    actor = await stepped_admin(env, make_actor)
    exact = {"plan": "instructor", "days": TRIAL_DAYS_MAX}
    assert (await put(actor, "trial", exact)).status_code == 200
    assert stored(superuser, "trial") == exact
    zero = await put(actor, "trial", {"plan": "instructor", "days": 0})  # off
    assert zero.status_code == 200


async def test_an_unknown_trial_plan_is_refused_without_logging_the_name(
    env: Env, make_actor: Any, superuser: Connection
) -> None:
    actor = await stepped_admin(env, make_actor)
    response = await put(actor, "trial", {"plan": "secretplanname", "days": 3})
    assert (response.status_code, reason(response)) == (422, "unknown_plan")
    assert stored(superuser, "trial") is None
    dump = superuser.execute(text("SELECT string_agg(params::text, ' ') FROM admin_audit")).scalar()
    assert "secretplanname" not in (dump or "")
    [row] = [r for r in rows_of(superuser, actor.user_id) if r.action == "setting.set"]
    assert (row.outcome, row.reason) == ("refused", "unknown_plan")


async def test_a_valid_banner_of_280_multibyte_characters_is_accepted(
    env: Env, make_actor: Any, superuser: Connection
) -> None:
    """The 2048-byte limit of ``app_settings`` is measured on UTF-8 bytes as jsonb prints them,
    not on an ASCII-escaped encoding, so 280 emoji (1120 bytes) fit."""
    actor = await stepped_admin(env, make_actor)
    banner = {"text": "🚴" * 280, "level": "info"}
    assert (await put(actor, "banner", banner)).status_code == 200
    assert stored(superuser, "banner") == banner
    assert (await Browser(env).get(PUBLIC)).json()["banner"] == banner


async def test_the_settings_read_runs_in_a_read_only_transaction(
    env: Env, make_actor: Any, superuser: Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    from classbeat.admin import settings as settings_module

    actor = await stepped_admin(env, make_actor)

    async def writes(session: Any, _settings: Any) -> None:
        await session.execute(
            text("INSERT INTO app_settings (key, value) VALUES ('banner', '\"x\"'::jsonb)")
        )

    monkeypatch.setattr(settings_module, "_view", writes)
    response = await actor.get(SETTINGS)
    assert response.status_code == 500  # the write was refused by the database
    assert stored(superuser, "banner") is None
