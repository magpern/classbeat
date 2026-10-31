"""M5 security suite, part 2 (#227): the admin area across **every** ``/api/admin`` route.

- The gate on every route, enumerated from the app (not a hand list): 403 ``forbidden`` with no
  audit row for a non-admin, ``mfa_required`` and (non-GET) ``stepup_required`` with exactly one
  ``refused`` row each.
- Input fuzz (PR #282 review): NUL, another control character and an over-long non-ASCII value
  in every path parameter, query parameter and top-level body field of every route. Each is a
  422 or 404 with exactly one audit row (``refused``), never a 500 and never an ``ok`` row; a
  422's row stores the route's template and method, never the raw input (#303 review).
- The planted-value test across every admin route is ``test_m5_security_admin_secrets.py``.

A route added later has no valid request in ``BODIES`` or its path values in
``target_with_entitlement`` until its PR adds them: the tests then fail and name it, so the fuzz
cannot silently skip a route. AD3's usage, jobs and refund routes, AD2's invites and redeem
codes and AD4's settings and plan limits (#314) are covered.

Threats covered: T90, T92, T95.
"""

import secrets
import uuid
from collections.abc import AsyncIterator, Callable
from typing import Any, get_args, get_origin
from urllib.parse import quote, urlencode

import pytest
from fastapi.routing import APIRoute
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.engine import Connection, make_url

from authtools import Env, running_env
from classbeat import auth
from classbeat.admin.audit import RefusalLimiter
from classbeat.admin.routes import admin_area_routes

WHOAMI = "/api/admin/whoami"  # M1's route: require_admin only, no audit row (ADR 0003 s. 7)
BAD_VALUES = {
    "nul": "a\x00b",
    "control": "a\x1bb",
    "long_non_ascii": "é" * 2000,  # 2000 characters, 12000 bytes once JSON-encoded
}
# A valid request for every route, so each fuzzed field is the only thing wrong with it.
BODIES: dict[tuple[str, str], dict[str, Any]] = {
    ("POST", "/api/admin/users/{user_id}/entitlements"): {"plan_id": "pro"},
    ("DELETE", "/api/admin/users/{user_id}"): {"email": "wrong@example.com"},
    # AD2 (#279): the invite and redeem-code creates.
    ("POST", "/api/admin/invites"): {"expires_days": 7},
    ("POST", "/api/admin/redeem-codes"): {
        "label": "Spring",
        "plan_id": "pro",
        "features": [],
        "grant_days": 30,
        "max_redemptions": 5,
        "expires_at": "2999-01-01T00:00:00+00:00",
    },
    # AD4 (#314): a flag write (the banner, path value ``key``) and a plan's limits (``plan_id``).
    ("PUT", "/api/admin/settings/{key}"): {"value": {"text": "Fuzz banner", "level": "info"}},
    ("PATCH", "/api/admin/plans/{plan_id}"): {"classes_per_month": 10},
}
# The path values that need no row of their own: a flag key and a plan id.
FIXED_VALUES = {"key": "banner", "plan_id": "pro"}


@pytest.fixture
async def env(
    app_database_url: str, maint_database_url: str, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[Env]:
    """The app with maint, the admin rate limit off and the refusal-row limiter raised: this
    file sends far more than 60 requests and 20 refusals a minute, and every one must leave its
    row (the limits themselves are tested in ``test_admin_routes_guard.py``)."""
    url = make_url(maint_database_url)
    monkeypatch.setattr(auth, "admin_buckets", lambda _user_id: [])
    async with running_env(
        app_database_url,
        maint_database_url=url.set(password=None).render_as_string(hide_password=False),
        database_maint_password=url.password,
    ) as env:
        env.app.state.admin_db.limiter = RefusalLimiter(per_admin=100_000, total=100_000)
        yield env


def routes(env: Env) -> list[tuple[str, APIRoute]]:
    """``(method, route)`` for every admin route but ``whoami``."""
    found = [
        (method, route)
        for path, route in admin_area_routes(env.app.routes)
        if path != WHOAMI and isinstance(route, APIRoute)
        for method in sorted(route.methods or ())
    ]
    # The Users screen's seven, the audit view, and AD3's usage, jobs and refund, at least.
    assert len(found) >= 11
    return found


def fill(route: APIRoute, values: dict[str, str], override: tuple[str, str] | None = None) -> str:
    path = route.path
    for param in route.dependant.path_params:
        value = override[1] if override and override[0] == param.name else values.get(param.name)
        assert value is not None, f"{route.path}: add a valid value for {{{param.name}}}"
        path = path.replace("{" + param.name + "}", quote(value, safe=""))
    return path


def body_model(route: APIRoute) -> type[BaseModel] | None:
    params = route.dependant.body_params
    if not params:
        return None
    model = params[0].field_info.annotation
    assert isinstance(model, type), route.path
    assert issubclass(model, BaseModel), route.path
    return model


async def send(actor: Any, method: str, path: str, body: Any = None) -> Any:
    return await (actor.get(path) if method == "GET" else actor.call(method, path, body))


def rows(conn: Connection, actor_id: object) -> list[tuple[str, str, str | None]]:
    return [
        tuple(r)
        for r in conn.execute(
            text(
                "SELECT action, outcome, reason FROM admin_audit WHERE actor_id = :a "
                "ORDER BY created_at, id"
            ),
            {"a": actor_id},
        )
    ]


def last_params(conn: Connection, actor_id: object) -> Any:
    return conn.execute(
        text(
            "SELECT params FROM admin_audit WHERE actor_id = :a ORDER BY created_at DESC, id DESC "
            "LIMIT 1"
        ),
        {"a": actor_id},
    ).scalar_one()


async def admin(env: Env, make_actor: Any, *, mfa: bool = True, stepup: bool = True) -> Any:
    user_id = await env.user(f"admin-{secrets.token_hex(4)}@example.com", role="admin")
    actor = await make_actor(user_id, mfa=mfa)
    if stepup:
        await env.run("UPDATE sessions SET stepup_at = now() WHERE user_id = :u", u=user_id)
    actor.user_id = user_id
    return actor


async def target_with_entitlement(env: Env, conn: Connection) -> dict[str, str]:
    """A user with a live entitlement: the path values every route gets."""
    target = await env.user(f"rider-{secrets.token_hex(4)}@example.com")
    entitlement = conn.execute(
        text(
            "INSERT INTO entitlements (owner_id, plan_id, source) VALUES (:o, 'pro', 'grant') "
            "RETURNING id"
        ),
        {"o": target},
    ).scalar_one()
    ledger = conn.execute(
        text(
            "INSERT INTO credit_ledger (owner_id, period, plan_id, state) VALUES (:o, "
            "CAST(date_trunc('month', now() AT TIME ZONE 'UTC') AS date), 'pro', 'committed') "
            "RETURNING id"
        ),
        {"o": target},
    ).scalar_one()
    invite = conn.execute(
        text(
            "INSERT INTO invites (code_hash, expires_at) VALUES (:h, now() + interval '7 days') "
            "RETURNING id"
        ),
        {"h": secrets.token_bytes(32)},
    ).scalar_one()
    code = conn.execute(
        text(
            "INSERT INTO redeem_codes (code_hash, label, plan_id, grant_days, max_redemptions, "
            "expires_at) VALUES (:h, 'Fuzz', 'pro', 30, 5, now() + interval '30 days') "
            "RETURNING id"
        ),
        {"h": secrets.token_bytes(32)},
    ).scalar_one()
    return {
        "user_id": str(target),
        "entitlement_id": str(entitlement),
        "ledger_id": str(ledger),
        "invite_id": str(invite),
        "code_id": str(code),
        **FIXED_VALUES,
    }


# --- the gate on every route ------------------------------------------------------------------


async def test_every_admin_route_refuses_a_non_admin_and_audits_the_admin_refusals(
    env: Env, make_actor: Any, superuser: Connection
) -> None:
    """Threats: T90, T92."""
    values = {"user_id": str(uuid.uuid4()), "entitlement_id": str(uuid.uuid4()),
              "ledger_id": str(uuid.uuid4()), "invite_id": str(uuid.uuid4()),
              "code_id": str(uuid.uuid4()), **FIXED_VALUES}  # fmt: skip
    plain = await env.user("plain@example.com")
    plain_actor = await make_actor(plain, mfa=True)
    await env.run("UPDATE sessions SET stepup_at = now() WHERE user_id = :u", u=plain)
    no_mfa = await admin(env, make_actor, mfa=False, stepup=False)
    no_stepup = await admin(env, make_actor, stepup=False)
    for method, route in routes(env):
        path, body = fill(route, values), BODIES.get((method, route.path))
        response = await send(plain_actor, method, path, body)
        assert (response.status_code, response.json()["detail"]["reason"]) == (403, "forbidden")
        before = len(rows(superuser, no_mfa.user_id))
        response = await send(no_mfa, method, path, body)
        assert (response.status_code, response.json()["detail"]["reason"]) == (
            403,
            "mfa_required",
        ), (method, route.path)
        assert rows(superuser, no_mfa.user_id)[before:] == [
            ("admin.request", "refused", "mfa_required")
        ]
        if method == "GET":
            continue
        before = len(rows(superuser, no_stepup.user_id))
        response = await send(no_stepup, method, path, body)
        assert (response.status_code, response.json()["detail"]["reason"]) == (
            403,
            "stepup_required",
        ), (method, route.path)
        assert rows(superuser, no_stepup.user_id)[before:] == [
            ("admin.request", "refused", "stepup_required")
        ]
    count = superuser.execute(
        text("SELECT count(*) FROM admin_audit WHERE actor_id = :a"), {"a": plain}
    ).scalar_one()
    assert count == 0  # a non-admin never gets a row (nor a maint connection)


# --- input fuzz across every route ------------------------------------------------------------


def settings_and_plans(conn: Connection) -> tuple[list[Any], list[Any]]:
    """Every stored flag and every plan's limits, with who and when last changed them."""
    settings = conn.execute(
        text("SELECT key, value, updated_at, updated_by FROM app_settings ORDER BY key")
    ).all()
    plans = conn.execute(
        text(
            "SELECT id, classes_per_month, revisions_per_class, concurrent_jobs, "
            "monthly_cost_micros, updated_at, updated_by FROM plans ORDER BY id"
        )
    ).all()
    return [tuple(r) for r in settings], [tuple(r) for r in plans]


def fuzz_cases(
    route: APIRoute, method: str
) -> list[tuple[str, Callable[[str], tuple[tuple[str, str] | None, dict[str, Any] | None, Any]]]]:
    """``(field, case)`` pairs; ``case(bad)`` gives (path override, query, body)."""
    base = BODIES.get((method, route.path))
    model = body_model(route)
    assert model is None or base is not None, f"{method} {route.path}: add a valid body to BODIES"
    cases: list[Any] = []
    for param in route.dependant.path_params:
        cases.append((f"path:{param.name}", lambda bad, n=param.name: ((n, bad), None, base)))
    for param in route.dependant.query_params:
        cases.append((f"query:{param.alias}", lambda bad, n=param.alias: (None, {n: bad}, base)))
    for name, info in (model.model_fields if model else {}).items():
        is_list = get_origin(info.annotation) is list or any(
            get_origin(a) is list for a in get_args(info.annotation)
        )
        cases.append(
            (
                f"body:{name}",
                lambda bad, n=name, many=is_list: (
                    None,
                    None,
                    {**(base or {}), n: [bad] if many else bad},
                ),
            )
        )
    return cases


async def test_every_string_input_of_every_admin_route_is_refused_and_audited_once(
    env: Env, make_actor: Any, superuser: Connection
) -> None:
    """Threats: T90, T92, T95."""
    actor = await admin(env, make_actor)
    values = await target_with_entitlement(env, superuser)
    settings_before, plans_before = settings_and_plans(superuser)
    tried: list[str] = []
    for method, route in routes(env):
        for field, case in fuzz_cases(route, method):
            for label, bad in BAD_VALUES.items():
                override, query, body = case(bad)
                path = fill(route, values, override)
                if query:
                    path += "?" + urlencode(query)
                before = len(rows(superuser, actor.user_id))
                response = await send(actor, method, path, body)
                where = f"{method} {route.path} {field} {label}"
                assert response.status_code in (404, 422), (where, response.text[:300])
                new = rows(superuser, actor.user_id)[before:]
                assert [row[1] for row in new] == ["refused"], (where, new)
                if new[0][0] == "admin.request":  # a 422: the template, never the raw input
                    assert last_params(superuser, actor.user_id) == {
                        "method": method,
                        "route": route.path,
                    }, where
                tried.append(where)
    assert any(
        t.startswith("POST /api/admin/users/{user_id}/entitlements body:note") for t in tried
    )
    assert any("/api/admin/audit query:actor_email" in t for t in tried)
    for ad3 in (
        "GET /api/admin/usage query:month",
        "GET /api/admin/jobs query:days",
        "POST /api/admin/users/{user_id}/credits/{ledger_id}/refund path:ledger_id",
        "POST /api/admin/invites body:expires_days",
        "POST /api/admin/invites/{invite_id}/disable path:invite_id",
        "GET /api/admin/redeem-codes query:offset",
        "POST /api/admin/redeem-codes body:label",
        "POST /api/admin/redeem-codes/{code_id}/disable path:code_id",
        "PUT /api/admin/settings/{key} path:key",
        "PUT /api/admin/settings/{key} body:value",
        "DELETE /api/admin/settings/{key} path:key",
        "PATCH /api/admin/plans/{plan_id} path:plan_id",
        "PATCH /api/admin/plans/{plan_id} body:classes_per_month",
    ):
        assert any(t.startswith(ad3) for t in tried), ad3  # fmt: skip
    state = superuser.execute(
        text("SELECT state FROM credit_ledger WHERE id = :l"), {"l": values["ledger_id"]}
    ).scalar_one()
    assert state == "committed"  # no fuzzed refund went through
    live = superuser.execute(
        text(
            "SELECT (SELECT expires_at > now() FROM invites WHERE id = :i), "
            "(SELECT disabled_at IS NULL FROM redeem_codes WHERE id = :c), "
            "(SELECT count(*) FROM redeem_codes WHERE label <> 'Fuzz')"
        ),
        {"i": values["invite_id"], "c": values["code_id"]},
    ).one()
    assert tuple(live) == (True, True, 0)  # nothing disabled, no fuzzed code created
    # No fuzzed flag write or plan edit changed a setting or a plan's limits.
    assert settings_and_plans(superuser) == (settings_before, plans_before)
    # Nothing the fuzz sent changed the target (read as the superuser, past RLS).
    kept = superuser.execute(
        text(
            "SELECT u.disabled_at IS NULL, (SELECT count(*) FROM entitlements e WHERE "
            "e.owner_id = u.id AND e.revoked_at IS NULL) FROM users u WHERE u.id = :u"
        ),
        {"u": values["user_id"]},
    ).one()
    assert tuple(kept) == (True, 1)


@pytest.mark.parametrize(
    ("path_suffix", "query", "reason"),
    [
        ("", {"limit": "0"}, "invalid_input"),  # FastAPI's own 422
        ("", {"since": "2026-10-02T00:00:00Z", "until": "2026-10-01T00:00:00Z"}, "period_invalid"),
    ],
)
async def test_an_input_refusal_after_the_gate_writes_one_refused_row(
    env: Env, make_actor: Any, superuser: Connection, path_suffix: str, query: dict[str, str],
    reason: str,
) -> None:  # fmt: skip
    """ADR 0011 section 10 (PR #281 review, Architect): an admin request that passes the gate
    and is refused for its input leaves a ``refused`` row, FastAPI's validation 422 and a route's
    own 422 raised before its session alike, with the route and method as params.

    Threats: T92.
    """
    actor = await admin(env, make_actor)
    response = await actor.get(f"/api/admin/audit{path_suffix}?{urlencode(query)}")
    assert response.status_code == 422, response.text
    found = superuser.execute(
        text("SELECT action, outcome, reason, params FROM admin_audit WHERE actor_id = :a"),
        {"a": actor.user_id},
    ).all()
    assert [tuple(r) for r in found] == [
        ("admin.request", "refused", reason, {"method": "GET", "route": "/api/admin/audit"})
    ]


async def test_a_refusal_inside_a_session_is_not_audited_twice(
    env: Env, make_actor: Any, superuser: Connection
) -> None:
    """``admin_session`` writes the row of its own ``AdminRefused``; the handler adds none.

    Threats: T92.
    """
    actor = await admin(env, make_actor)
    response = await actor.get(f"/api/admin/users/{uuid.uuid4()}")
    assert response.status_code == 404
    assert rows(superuser, actor.user_id) == [("user.read", "refused", "not_found")]


async def test_a_nul_in_the_grant_note_is_422_not_a_500(
    env: Env, make_actor: Any, superuser: Connection
) -> None:
    """PR #282 final review: ``entitlements.note`` refuses a NUL, which was a 500 with an
    ``error`` row. The note is now ``bounded_text``: 422, one ``refused`` row, nothing stored.

    Threats: T95.
    """
    actor = await admin(env, make_actor)
    target = await env.user("t@example.com")
    for note in ("a\x00b", "line\nbreak", "\x7f"):
        response = await actor.post(
            f"/api/admin/users/{target}/entitlements", {"plan_id": "pro", "note": note}
        )
        assert response.status_code == 422, response.text
    assert rows(superuser, actor.user_id) == [("admin.request", "refused", "invalid_input")] * 3
    stored = superuser.execute(
        text("SELECT count(*) FROM entitlements WHERE owner_id = :o"), {"o": target}
    ).scalar_one()
    assert stored == 0
    fine = await actor.post(
        f"/api/admin/users/{target}/entitlements", {"plan_id": "pro", "note": "é" * 200}
    )
    assert fine.status_code == 201, fine.text
