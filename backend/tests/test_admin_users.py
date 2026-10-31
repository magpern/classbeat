"""The admin Users screen (ADR 0011 section 6, AD1 #218): the gate on every route, the audit row
of every call, list and detail, grants and revokes, disable and enable, delete, admin targets
refused, no route changing ``users.role``, and a planted-value test over every response."""

import json
import secrets
import uuid
from collections.abc import AsyncIterator
from typing import Any
from urllib.parse import urlencode

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Connection, make_url

from authtools import Env, running_env
from classbeat.admin.routes import admin_area_routes, gate_problems
from classbeat.connections import CATEGORIES

USERS = "/api/admin/users"


@pytest.fixture
async def env(app_database_url: str, maint_database_url: str) -> AsyncIterator[Env]:
    url = make_url(maint_database_url)
    async with running_env(
        app_database_url,
        maint_database_url=url.set(password=None).render_as_string(hide_password=False),
        database_maint_password=url.password,
    ) as env:
        yield env


def reason(response: Any) -> str:
    return response.json()["detail"]["reason"]


def audit(conn: Connection, actor_id: object) -> list[tuple[str, str, str | None]]:
    """``(action, outcome, reason)`` of ``actor_id``'s audit rows, oldest first."""
    return [
        (r.action, r.outcome, r.reason)
        for r in conn.execute(
            text(
                "SELECT action, outcome, reason FROM admin_audit WHERE actor_id = :u "
                "ORDER BY created_at, id"
            ),
            {"u": actor_id},
        )
    ]


async def stepped_admin(env: Env, make_actor: Any, email: str = "a@example.com") -> Any:
    """An admin with TOTP in the last 12 hours and a fresh step-up."""
    user_id = await env.user(email, role="admin")
    actor = await make_actor(user_id, mfa=True)
    await env.run("UPDATE sessions SET stepup_at = now() WHERE user_id = :u", u=user_id)
    actor.user_id = user_id
    return actor


def routes_for(user_id: object, entitlement_id: object) -> list[tuple[str, str, Any]]:
    """Every Users route as (method, path, body)."""
    base = f"{USERS}/{user_id}"
    return [
        ("GET", USERS, None),
        ("GET", base, None),
        ("POST", f"{base}/entitlements", {"plan_id": "pro"}),
        ("POST", f"{base}/entitlements/{entitlement_id}/revoke", None),
        ("POST", f"{base}/disable", None),
        ("POST", f"{base}/enable", None),
        ("DELETE", base, {"email": "x@example.com"}),
    ]


async def send(actor: Any, method: str, path: str, body: Any = None) -> Any:
    return await (actor.get(path) if method == "GET" else actor.call(method, path, body))


async def test_the_router_guard_covers_the_users_routes(env: Env) -> None:
    assert gate_problems(env.app.routes) == []
    paths = {path for path, _ in admin_area_routes(env.app.routes)}
    assert {
        USERS,
        f"{USERS}/{{user_id}}",
        f"{USERS}/{{user_id}}/entitlements",
        f"{USERS}/{{user_id}}/entitlements/{{entitlement_id}}/revoke",
        f"{USERS}/{{user_id}}/disable",
        f"{USERS}/{{user_id}}/enable",
    } <= paths


async def test_a_non_admin_gets_403_on_every_route_and_no_audit_row(
    env: Env, make_actor: Any, superuser: Connection
) -> None:
    plain = await env.user("plain@example.com")
    actor = await make_actor(plain, mfa=True)
    await env.run("UPDATE sessions SET stepup_at = now()")
    for method, path, body in routes_for(plain, uuid.uuid4()):
        response = await send(actor, method, path, body)
        assert (response.status_code, reason(response)) == (403, "forbidden"), path
    assert superuser.execute(text("SELECT count(*) FROM admin_audit")).scalar_one() == 0
    assert await env.scalar("SELECT disabled_at FROM users WHERE id = :u", u=plain) is None


async def test_mfa_and_stepup_refusals_are_audited(
    env: Env, make_actor: Any, superuser: Connection
) -> None:
    admin = await env.user("a@example.com", role="admin")
    target = await env.user("t@example.com")
    no_mfa = await make_actor(admin)
    for method, path, body in routes_for(target, uuid.uuid4()):
        response = await send(no_mfa, method, path, body)
        assert (response.status_code, reason(response)) == (403, "mfa_required"), path
    no_stepup = await make_actor(admin, mfa=True)
    writes = [r for r in routes_for(target, uuid.uuid4()) if r[0] != "GET"]
    for method, path, body in writes:
        response = await send(no_stepup, method, path, body)
        assert (response.status_code, reason(response)) == (403, "stepup_required"), path
    assert audit(superuser, admin) == [("admin.request", "refused", "mfa_required")] * 7 + [
        ("admin.request", "refused", "stepup_required")
    ] * len(writes)
    assert await env.scalar("SELECT count(*) FROM entitlements") == 0
    assert await env.scalar("SELECT disabled_at FROM users WHERE id = :u", u=target) is None


async def test_an_unknown_user_is_404_on_every_route(
    env: Env, make_actor: Any, superuser: Connection
) -> None:
    actor = await stepped_admin(env, make_actor)
    ghost = uuid.uuid4()
    for method, path, body in routes_for(ghost, uuid.uuid4())[1:]:
        response = await send(actor, method, path, body)
        assert (response.status_code, reason(response)) == (404, "not_found"), path
        assert response.headers["cache-control"] == "no-store"
    assert [row[1:] for row in audit(superuser, actor.user_id)] == [("refused", "not_found")] * 6
    # The unknown id is kept as text; no foreign key points at a user that does not exist.
    read = superuser.execute(
        text(
            "SELECT target_user_id, target_kind, target_id FROM admin_audit "
            "WHERE action = 'user.read'"
        )
    ).one()
    assert tuple(read) == (None, "user", str(ghost))


async def test_the_list_searches_by_prefix_pages_and_summarises_the_plan(
    env: Env, make_actor: Any, superuser: Connection
) -> None:
    actor = await stepped_admin(env, make_actor, "admin@example.com")
    ann = await env.user("ana@example.com")
    for name in ("anb", "anc", "bob", "a_c"):
        await env.user(f"{name}@example.com")
    superuser.execute(
        text("INSERT INTO entitlements (owner_id, plan_id, source) VALUES (:o, 'pro', 'grant')"),
        {"o": ann},
    )
    superuser.execute(
        text(
            "INSERT INTO credit_ledger (owner_id, period, plan_id, state) VALUES (:o, "
            "CAST(date_trunc('month', now() AT TIME ZONE 'UTC') AS date), 'pro', 'committed')"
        ),
        {"o": ann},
    )
    first = await actor.get(f"{USERS}?q=AN&limit=2")
    assert first.status_code == 200, first.text
    page = first.json()
    assert [u["email"] for u in page["users"]] == ["ana@example.com", "anb@example.com"]
    assert page["next_offset"] == 2
    top = page["users"][0]
    assert top["plan"] == {"id": "pro", "name": top["plan"]["name"], "source": "grant",
                           "ends_at": None}  # fmt: skip
    assert (top["classes_held"], top["classes_limit"], top["cost_micros"]) == (1, 100, 0)
    second = (await actor.get(f"{USERS}?q=an&offset=2&limit=2")).json()
    assert [u["email"] for u in second["users"]] == ["anc@example.com"]
    assert second["next_offset"] is None
    assert second["users"][0]["plan"]["id"] == "free"
    # ``_`` is a literal in the prefix, not a wildcard.
    literal = (await actor.get(f"{USERS}?q=a_")).json()
    assert [u["email"] for u in literal["users"]] == ["a_c@example.com"]
    params = superuser.execute(
        text("SELECT params FROM admin_audit WHERE actor_id = :u AND action = 'user.list' "
             "ORDER BY created_at LIMIT 1"),
        {"u": actor.user_id},
    ).scalar_one()  # fmt: skip
    assert params == {"q": "AN", "offset": 0, "limit": 2}
    assert audit(superuser, actor.user_id) == [("user.list", "ok", None)] * 3


async def test_the_detail_shows_entitlements_three_periods_and_connection_status(
    env: Env, make_actor: Any, superuser: Connection
) -> None:
    actor = await stepped_admin(env, make_actor)
    target = await env.user("t@example.com")
    superuser.execute(
        text(
            "INSERT INTO entitlements (owner_id, plan_id, source, note) "
            "VALUES (:o, 'instructor', 'grant', 'beta tester')"
        ),
        {"o": target},
    )
    for months_ago in (0, 2, 3):
        superuser.execute(
            text(
                "INSERT INTO credit_ledger (owner_id, period, plan_id, state) VALUES (:o, "
                "CAST(date_trunc('month', now() AT TIME ZONE 'UTC') - make_interval(months => :m)"
                " AS date), 'free', 'committed')"
            ),
            {"o": target, "m": months_ago},
        )
    superuser.execute(
        text(
            "INSERT INTO connections (owner_id, provider, status, last_error, secret, "
            "secret_nonce, wrapped_dek, dek_nonce, key_id) VALUES (:o, 'mowl', "
            "'needs_reconnect', :e, '\\x00', :n, :w, :n, 'kek-1')"
        ),
        {"o": target, "e": CATEGORIES[0], "n": bytes(12), "w": bytes(48)},
    )
    response = await actor.get(f"{USERS}/{target}")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["plan"]["id"] == "instructor"
    assert [(e["note"], e["active"]) for e in body["entitlements"]] == [("beta tester", True)]
    assert len(body["ledger"]) == 2  # this month and two months ago, not three
    statuses = {c["provider"]: (c["status"], c["last_error"]) for c in body["connections"]}
    assert statuses == {
        "mowl": ("needs_reconnect", CATEGORIES[0]),
        "spotify": ("not_connected", None),
        "swedebeat": ("not_connected", None),
    }
    assert audit(superuser, actor.user_id) == [("user.read", "ok", None)]


async def test_a_grant_shows_in_the_targets_plan_at_once_and_a_revoke_ends_it(
    env: Env, make_actor: Any, superuser: Connection
) -> None:
    actor = await stepped_admin(env, make_actor)
    target = await env.user("t@example.com")
    rider = await make_actor(target)
    assert (await rider.get("/api/account/plan")).json()["plan"] == "free"
    granted = await actor.post(
        f"{USERS}/{target}/entitlements",
        {"plan_id": "instructor", "features": ["song_picking"], "note": "tester"},
    )
    assert granted.status_code == 201, granted.text
    entitlement = granted.json()
    assert (entitlement["source"], entitlement["granted_by"]) == ("grant", str(actor.user_id))
    plan = (await rider.get("/api/account/plan")).json()
    assert (plan["plan"], plan["source"], plan["features"]) == (
        "instructor", "grant", ["song_picking"]
    )  # fmt: skip
    revoke = f"{USERS}/{target}/entitlements/{entitlement['id']}/revoke"
    revoked = await actor.post(revoke)
    assert revoked.status_code == 200, revoked.text
    assert revoked.json()["active"] is False
    assert (await rider.get("/api/account/plan")).json()["plan"] == "free"
    again = await actor.post(revoke)
    assert (again.status_code, reason(again)) == (409, "already_revoked")
    other = await env.user("o@example.com")
    elsewhere = await actor.post(f"{USERS}/{other}/entitlements/{entitlement['id']}/revoke")
    assert (elsewhere.status_code, reason(elsewhere)) == (404, "not_found")
    assert [r[:2] for r in audit(superuser, actor.user_id)] == [
        ("user.grant", "ok"), ("user.revoke", "ok"), ("user.revoke", "refused"),
        ("user.revoke", "refused"),
    ]  # fmt: skip
    params = superuser.execute(
        text("SELECT params FROM admin_audit WHERE action = 'user.grant'")
    ).scalar_one()
    assert params == {"plan_id": "instructor", "features": ["song_picking"], "ends_at": None,
                      "note": True}  # fmt: skip


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"plan_id": "gold"},
        {"features": ["swedebeat"]},
        {"plan_id": "pro", "ends_at": "2020-01-01T00:00:00+00:00"},
        {"plan_id": "pro", "ends_at": "2999-01-01T00:00:00"},  # no time zone
        {"plan_id": "pro", "note": "x" * 201},
        {"plan_id": "pro", "role": "admin"},
        {"plan_id": "pro", "source": "code"},
    ],
)
async def test_a_bad_grant_writes_nothing(env: Env, make_actor: Any, body: Any) -> None:
    actor = await stepped_admin(env, make_actor)
    target = await env.user("t@example.com")
    response = await actor.post(f"{USERS}/{target}/entitlements", body)
    assert response.status_code == 422, response.text
    assert await env.scalar("SELECT count(*) FROM entitlements") == 0
    assert await env.scalar("SELECT role FROM users WHERE id = :u", u=target) == "user"


async def test_disable_revokes_the_sessions_and_enable_lets_the_user_back(
    env: Env, make_actor: Any, superuser: Connection
) -> None:
    actor = await stepped_admin(env, make_actor)
    target = await env.user("t@example.com")
    rider = await make_actor(target)
    second = await make_actor(target)
    assert (await rider.get("/api/account/plan")).status_code == 200
    disabled = await actor.post(f"{USERS}/{target}/disable")
    assert disabled.status_code == 200, disabled.text
    assert disabled.json()["sessions_revoked"] == 2
    for browser in (rider, second):
        assert (await browser.get("/api/account/plan")).status_code == 401
    assert await env.scalar("SELECT count(*) FROM sessions WHERE user_id = :u", u=target) == 0
    listed = (await actor.get(f"{USERS}?q=t@")).json()["users"]
    assert listed[0]["disabled_at"] is not None
    enabled = await actor.post(f"{USERS}/{target}/enable")
    assert enabled.status_code == 200, enabled.text
    assert await env.scalar("SELECT disabled_at FROM users WHERE id = :u", u=target) is None
    back = await make_actor(target)
    assert (await back.get("/api/account/plan")).status_code == 200
    assert [r[:2] for r in audit(superuser, actor.user_id)] == [
        ("user.disable", "ok"), ("user.list", "ok"), ("user.enable", "ok"),
    ]  # fmt: skip


@pytest.mark.parametrize("who", ["self", "other_admin"])
async def test_disabling_or_deleting_an_admin_is_409_and_changes_nothing(
    env: Env, make_actor: Any, superuser: Connection, who: str
) -> None:
    actor = await stepped_admin(env, make_actor)
    target = actor.user_id if who == "self" else await env.user("b@example.com", role="admin")
    email = await env.scalar("SELECT email FROM users WHERE id = :u", u=target)
    sessions = await env.scalar("SELECT count(*) FROM sessions WHERE user_id = :u", u=target)
    for method, path, body in (
        ("POST", f"{USERS}/{target}/disable", None),
        ("DELETE", f"{USERS}/{target}", {"email": email}),
    ):
        response = await actor.call(method, path, body)
        assert (response.status_code, reason(response)) == (409, "target_is_admin"), path
    row = await env.row("SELECT role, disabled_at FROM users WHERE id = :u", u=target)
    assert (row.role, row.disabled_at) == ("admin", None)
    assert await env.scalar("SELECT count(*) FROM sessions WHERE user_id = :u", u=target) == (
        sessions
    )
    assert audit(superuser, actor.user_id) == [
        ("user.disable", "refused", "target_is_admin"),
        ("user.delete", "refused", "target_is_admin"),
    ]


async def test_delete_needs_the_typed_email_and_removes_the_account_and_its_rows(
    env: Env, make_actor: Any, superuser: Connection
) -> None:
    actor = await stepped_admin(env, make_actor)
    target = await env.user("Rider@Example.com")
    superuser.execute(
        text("INSERT INTO entitlements (owner_id, plan_id, source) VALUES (:o, 'pro', 'grant')"),
        {"o": target},
    )
    class_id = uuid.uuid4()
    superuser.execute(
        text("INSERT INTO classes (id, owner_id, title) VALUES (:c, :o, 'Ride')"),
        {"c": class_id, "o": target},
    )
    superuser.execute(
        text(
            "INSERT INTO jobs (owner_id, kind, state, class_id, input, idempotency_key, "
            "request_hash, credit_state) VALUES (:o, 'test_kind', 'queued', :c, '{}', :k, :h, "
            "'reserved')"
        ),
        {"o": target, "c": class_id, "k": secrets.token_urlsafe(18), "h": bytes(32)},
    )
    superuser.execute(
        text(
            "INSERT INTO credit_ledger (owner_id, job_id, period, plan_id, state) SELECT :o, id, "
            "CAST(date_trunc('month', now() AT TIME ZONE 'UTC') AS date), 'free', 'reserved' "
            "FROM jobs WHERE owner_id = :o"
        ),
        {"o": target},
    )
    wrong = await actor.call("DELETE", f"{USERS}/{target}", {"email": "other@example.com"})
    assert (wrong.status_code, reason(wrong)) == (409, "email_mismatch")
    assert await env.scalar("SELECT count(*) FROM users WHERE id = :u", u=target) == 1
    done = await actor.call("DELETE", f"{USERS}/{target}", {"email": " rider@example.COM "})
    assert done.status_code == 204, done.text
    assert done.headers["cache-control"] == "no-store"
    for table, column in (("users", "id"), ("entitlements", "owner_id"), ("jobs", "owner_id"),
                          ("credit_ledger", "owner_id"), ("classes", "owner_id")):  # fmt: skip
        count = superuser.execute(
            text(f"SELECT count(*) FROM {table} WHERE {column} = :u"),  # noqa: S608
            {"u": target},
        ).scalar_one()
        assert count == 0, table
    row = superuser.execute(
        text(
            "SELECT target_user_id, target_kind, target_id FROM admin_audit "
            "WHERE action = 'user.delete' AND outcome = 'ok'"
        )
    ).one()
    assert tuple(row) == (None, "user", str(target))
    assert (await actor.get(f"{USERS}/{target}")).status_code == 404


async def test_no_route_changes_a_role(env: Env, make_actor: Any) -> None:
    actor = await stepped_admin(env, make_actor)
    target = await env.user("t@example.com")
    for method, path, body in routes_for(target, uuid.uuid4())[2:-1]:
        await send(actor, method, path, {**(body or {}), "role": "admin"} if body else None)
    await actor.post(f"{USERS}/{target}/entitlements", {"plan_id": "pro", "role": "admin"})
    await actor.call("DELETE", f"{USERS}/{target}", {"email": "x@example.com", "role": "admin"})
    roles = await env.row(
        "SELECT (SELECT role FROM users WHERE id = :t) AS target_role, "
        "(SELECT role FROM users WHERE id = :a) AS admin_role",
        t=target,
        a=actor.user_id,
    )
    assert (roles.target_role, roles.admin_role) == ("user", "admin")


async def test_no_admin_response_contains_a_planted_secret(
    env: Env, make_actor: Any, superuser: Connection
) -> None:
    """Tokens, ciphertexts, hashes, the Client ID, the brief, job input and the playlist id of a
    user are planted; no response of any Users route contains one (ADR 0011 section 10)."""
    actor = await stepped_admin(env, make_actor)
    target = await env.user("t@example.com")
    rider = await make_actor(target)
    plant = {
        "password": "$argon2id$v=19$PLANTEDpasswordhash",
        "client_id": "c0ffee" + secrets.token_hex(13),
        "secret": secrets.token_bytes(24),
        "brief": "PLANTED brief climbs " + secrets.token_hex(6),
        "input": "PLANTED input " + secrets.token_hex(6),
        "playlist": "7Planted" + secrets.token_hex(7),
        "spotify_user": "planted-rider-" + secrets.token_hex(4),
    }
    superuser.execute(
        text("UPDATE users SET password_hash = :p WHERE id = :o"),
        {"p": plant["password"], "o": target},
    )
    superuser.execute(
        text(
            "INSERT INTO connections (owner_id, provider, status, public, secret, secret_nonce, "
            "wrapped_dek, dek_nonce, key_id) VALUES (:o, 'spotify', 'connected', "
            "CAST(:pub AS jsonb), :s, :n, :w, :n, 'kek-1')"
        ),
        {
            "o": target,
            "pub": json.dumps({"client_id": plant["client_id"]}),
            "s": plant["secret"],
            "n": bytes(12),
            "w": bytes(48),
        },
    )
    class_id = uuid.uuid4()
    superuser.execute(
        text(
            "INSERT INTO classes (id, owner_id, title, brief) "
            "VALUES (:c, :o, 'Ride', CAST(:b AS jsonb))"
        ),
        {"c": class_id, "o": target, "b": json.dumps({"text": plant["brief"]})},
    )
    superuser.execute(
        text(
            "INSERT INTO jobs (owner_id, kind, state, class_id, input, idempotency_key, "
            "request_hash, credit_state) VALUES (:o, 'test_kind', 'queued', :c, "
            "CAST(:i AS jsonb), :k, :h, 'none')"
        ),
        {
            "o": target,
            "c": class_id,
            "i": json.dumps({"x": plant["input"]}),
            "k": secrets.token_urlsafe(18),
            "h": bytes(32),
        },
    )
    superuser.execute(
        text(
            "INSERT INTO class_playlists (owner_id, class_id, playlist_id, spotify_user_id, "
            "name, state) VALUES (:o, :c, :p, :s, 'Ride', 'created')"
        ),
        {"o": target, "c": class_id, "p": plant["playlist"], "s": plant["spotify_user"]},
    )
    hashes = superuser.execute(
        text("SELECT token_hash FROM sessions WHERE user_id IN (:a, :t)"),
        {"a": actor.user_id, "t": target},
    ).scalars()
    needles = [v for v in plant.values() if isinstance(v, str)] + [rider.token, actor.token]
    for raw in [plant["secret"], *hashes]:
        needles += [raw.hex(), bytes(raw).decode("latin-1")]
    granted = await actor.post(f"{USERS}/{target}/entitlements", {"plan_id": "pro"})
    responses = [
        await actor.get(USERS),
        await actor.get(f"{USERS}?q=t"),
        await actor.get(f"{USERS}/{target}"),
        granted,
        await actor.post(f"{USERS}/{target}/entitlements/{granted.json()['id']}/revoke"),
        await actor.post(f"{USERS}/{target}/disable"),
        await actor.post(f"{USERS}/{target}/enable"),
        await actor.call("DELETE", f"{USERS}/{target}", {"email": "wrong@example.com"}),
        await actor.call("DELETE", f"{USERS}/{actor.user_id}", {"email": "a@example.com"}),
    ]
    assert all(r.status_code < 500 for r in responses)
    for response in responses:
        body = response.text
        assert not [n for n in needles if n and n in body], response.request.url


# --- the search text's bounds (#282 review) -----------------------------------------------------


@pytest.mark.parametrize(
    ("q", "status"),
    [
        ("\\u0000", 200),  # a literal backslash and "u0000": six ordinary characters
        ("a\x00b", 422),  # a real NUL, which jsonb can't store
        ("a\x1bb", 422),  # any control character
        ("\U0001f600" * 254, 422),  # 254 characters, 3048 bytes once encoded
        ("é" * 170, 200),  # 1020 bytes once encoded: inside the bound
    ],
)
async def test_the_search_text_is_bounded_and_every_accepted_search_is_audited(
    env: Env, make_actor: Any, superuser: Connection, q: str, status: int
) -> None:
    actor = await stepped_admin(env, make_actor)
    response = await actor.get(f"{USERS}?{urlencode({'q': q})}")
    assert response.status_code == status, response.text
    # A refused search writes the gate's row, never an ``ok`` one (ADR 0011 section 10, #227).
    expected = (
        [("user.list", "ok", None)]
        if status == 200
        else [("admin.request", "refused", "invalid_input")]
    )
    assert audit(superuser, actor.user_id) == expected
    if status == 200:
        stored = superuser.execute(
            text("SELECT params->>'q' FROM admin_audit WHERE actor_id = :u"), {"u": actor.user_id}
        ).scalar_one()
        assert stored == q
