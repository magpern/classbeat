"""The admin Abuse view (#632 item 3): the gate (401, 403, mfa), the audit row, the three lists from
planted ``security_log`` rows (top users, limit hits, sessions seen from several networks), the
window, the data minimisation (no hash, address, session id, token, route or User-Agent in a
response), and the disable shortcut's audit."""

import ast
import re
import secrets
import uuid
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Connection, make_url

from adminviews import audit, reason, stepped_admin
from authtools import Env, running_env
from classbeat.admin import abuse
from classbeat.admin.routes import admin_area_routes, gate_problems

ABUSE = "/api/admin/abuse"
SRC = Path(__file__).resolve().parent.parent / "src" / "classbeat"


@pytest.fixture
async def env(app_database_url: str, maint_database_url: str) -> AsyncIterator[Env]:
    url = make_url(maint_database_url)
    async with running_env(
        app_database_url,
        maint_database_url=url.set(password=None).render_as_string(hide_password=False),
        database_maint_password=url.password,
    ) as env:
        yield env


def plant(
    conn: Connection,
    owner: uuid.UUID,
    *,
    session: uuid.UUID,
    net: bytes,
    status: int = 200,
    minutes_ago: float = 1,
    count: int = 1,
    limited_minute: int | None = None,
) -> None:
    for i in range(count):
        conn.execute(
            text(
                "INSERT INTO security_log (owner_id, session_id, ip_hash, method, route, status, "
                "at, limited_minute) VALUES (:o, :s, :n, 'GET', '/api/classes', :st, "
                "now() - make_interval(secs => :ago), :lm)"
            ),
            {
                "o": owner, "s": session, "n": net, "st": status,
                "ago": minutes_ago * 60 + i,
                "lm": limited_minute if limited_minute is not None else (
                    int(minutes_ago * 60 + i) + 7 if status == 429 else None
                ),
            },
        )  # fmt: skip


def net() -> bytes:
    return secrets.token_bytes(32)


async def test_the_router_guard_covers_the_abuse_route(env: Env) -> None:
    assert gate_problems(env.app.routes) == []
    assert ABUSE in {path for path, _ in admin_area_routes(env.app.routes)}


async def test_unauthenticated_is_401(env: Env, superuser: Connection) -> None:
    response = await env.client.get(ABUSE)
    assert (response.status_code, reason(response)) == (401, "not_authenticated")
    assert superuser.execute(text("SELECT count(*) FROM admin_audit")).scalar_one() == 0


async def test_non_admins_get_403_and_an_admin_without_mfa_is_audited(
    env: Env, make_actor: Any, superuser: Connection
) -> None:
    plain = await make_actor(await env.user("plain@example.com"), mfa=True)
    response = await plain.get(ABUSE)
    assert (response.status_code, reason(response)) == (403, "forbidden")
    assert plain.token not in response.text
    assert superuser.execute(text("SELECT count(*) FROM admin_audit")).scalar_one() == 0
    admin = await env.user("a@example.com", role="admin")
    no_mfa = await (await make_actor(admin)).get(ABUSE)
    assert (no_mfa.status_code, reason(no_mfa)) == (403, "mfa_required")
    assert audit(superuser, admin) == [("admin.request", "refused", "mfa_required")]


async def test_the_three_lists_from_planted_rows(
    env: Env, make_actor: Any, superuser: Connection
) -> None:
    actor = await stepped_admin(env, make_actor)
    busy = await env.user("busy@example.com")
    mover = await env.user("mover@example.com")
    calm = await env.user("calm@example.com")
    other_admin = await env.user("boss@example.com", role="admin")
    s_busy, s_mover, s_calm, s_old = (uuid.uuid4() for _ in range(4))
    home, mobile, third = net(), net(), net()
    # busy: 30 requests in the window, one network, two 429s in two minutes.
    plant(superuser, busy, session=s_busy, net=home, count=30, minutes_ago=2)
    plant(superuser, busy, session=s_busy, net=home, status=429, minutes_ago=3, limited_minute=1)
    plant(superuser, busy, session=s_busy, net=home, status=429, minutes_ago=4, limited_minute=2)
    # mover: one session from three networks, another session from one.
    for n in (home, mobile, third):
        plant(superuser, mover, session=s_mover, net=n, count=2, minutes_ago=10)
    plant(superuser, mover, session=s_old, net=home, minutes_ago=10)
    plant(superuser, calm, session=s_calm, net=home, count=2, minutes_ago=5)
    # outside the 60-minute window: counts nowhere.
    plant(superuser, calm, session=s_calm, net=mobile, count=5, minutes_ago=120)
    plant(superuser, other_admin, session=uuid.uuid4(), net=home, status=429, minutes_ago=1,
          limited_minute=3)  # fmt: skip

    response = await actor.get(ABUSE)
    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "no-store"
    body = response.json()
    assert (body["minutes"], body["requests"], body["rate_limited"], body["users"]) == (
        60, 30 + 2 + 6 + 1 + 2 + 1, 3, 4
    )  # fmt: skip
    top = body["top_users"]
    assert [u["email"] for u in top][:2] == ["busy@example.com", "mover@example.com"]
    assert (top[0]["requests"], top[0]["rate_limited"], top[0]["networks"]) == (32, 2, 1)
    assert (top[0]["sessions"], top[0]["per_minute"]) == (1, round(32 / 60, 2))
    assert top[0]["busiest_minute"] >= 1
    assert (top[1]["requests"], top[1]["networks"], top[1]["sessions"]) == (7, 3, 2)
    assert top[0]["role"] == "user"
    assert top[0]["disabled"] is False
    hits = {h["email"]: h for h in body["limit_hits"]}
    assert set(hits) == {"busy@example.com", "boss@example.com"}
    assert hits["busy@example.com"]["hits"] == 2
    assert hits["busy@example.com"]["first_at"] < hits["busy@example.com"]["last_at"]
    assert hits["boss@example.com"]["role"] == "admin"
    (moved,) = body["moved_sessions"]  # calm's two networks are outside the window
    assert (moved["email"], moved["networks"], moved["requests"]) == ("mover@example.com", 3, 6)
    assert not any(body[k] for k in (
        "top_users_truncated", "limit_hits_truncated", "moved_sessions_truncated"
    ))  # fmt: skip

    wider = (await actor.get(f"{ABUSE}?minutes=240")).json()
    # +5 planted, +1 the admin's own first read, which the log recorded after answering.
    assert wider["requests"] == body["requests"] + 5 + 1
    assert sorted(m["email"] for m in wider["moved_sessions"]) == [
        "calm@example.com", "mover@example.com"
    ]  # fmt: skip
    assert audit(superuser, actor.user_id) == [("abuse.list", "ok", None)] * 2
    params = superuser.execute(
        text("SELECT params FROM admin_audit WHERE action = 'abuse.list' ORDER BY created_at")
    ).scalars().all()  # fmt: skip
    assert params == [{"minutes": 60}, {"minutes": 240}]


async def test_responses_carry_no_hash_address_session_token_route_or_agent(
    env: Env, make_actor: Any, superuser: Connection
) -> None:
    actor = await stepped_admin(env, make_actor)
    user = await env.user("rider@example.com")
    session, a, b = uuid.uuid4(), net(), net()
    plant(superuser, user, session=session, net=a, count=3)
    plant(superuser, user, session=session, net=b, status=429, limited_minute=5)
    stored = [
        bytes(x) for x in superuser.execute(text("SELECT ip_hash FROM security_log")).scalars()
    ]
    raw = (await actor.get(ABUSE)).text
    needles = [str(session), actor.token, "/api/classes", "user-agent", "User-Agent", "192.0.2."]
    needles += [h.hex() for h in stored] + [stored[0].decode("latin-1")]
    assert [n for n in needles if n in raw] == []
    body = (await actor.get(ABUSE)).json()
    keys = {k for rows in (body["top_users"], body["limit_hits"], body["moved_sessions"])
            for row in rows for k in row} | set(body)  # fmt: skip
    assert not [k for k in keys if re.search(r"ip|addr|agent|token|session_id|route|path|body", k)
                and k != "sessions"]  # fmt: skip


async def test_a_bad_window_is_422_and_reads_nothing(
    env: Env, make_actor: Any, superuser: Connection
) -> None:
    actor = await stepped_admin(env, make_actor)
    for minutes in ("4", "241", "x"):
        response = await actor.get(f"{ABUSE}?minutes={minutes}")
        assert response.status_code == 422, response.text
    assert audit(superuser, actor.user_id) == [("admin.request", "refused", "invalid_input")] * 3


async def test_an_empty_log_answers_empty_lists(env: Env, make_actor: Any) -> None:
    actor = await stepped_admin(env, make_actor)
    assert (await actor.get(ABUSE)).json() == {
        "minutes": 60, "requests": 0, "rate_limited": 0, "users": 0, "top_users": [],
        "limit_hits": [], "moved_sessions": [], "top_users_truncated": False,
        "limit_hits_truncated": False, "moved_sessions_truncated": False,
    }  # fmt: skip


async def test_lists_stop_at_the_row_cap(env: Env, make_actor: Any, superuser: Connection) -> None:
    actor = await stepped_admin(env, make_actor)
    for i in range(abuse.LIST_ROWS + 1):
        user = await env.user(f"u{i}@example.com")
        plant(superuser, user, session=uuid.uuid4(), net=net(), count=i + 1)
    body = (await actor.get(ABUSE)).json()
    assert len(body["top_users"]) == abuse.LIST_ROWS
    assert body["top_users_truncated"] is True
    assert body["top_users"][0]["requests"] == abuse.LIST_ROWS + 1


async def test_the_disable_shortcut_signs_the_user_out_and_is_audited(
    env: Env, make_actor: Any, superuser: Connection
) -> None:
    actor = await stepped_admin(env, make_actor)
    target = await env.user("t@example.com")
    rider = await make_actor(target)
    plant(superuser, target, session=uuid.uuid4(), net=net(), count=3)
    listed = (await actor.get(ABUSE)).json()["top_users"][0]
    assert (listed["user_id"], listed["disabled"]) == (str(target), False)
    done = await actor.post(f"/api/admin/users/{listed['user_id']}/disable")
    assert done.status_code == 200, done.text
    assert (await rider.get("/api/account/plan")).status_code == 401
    rows = (await actor.get(ABUSE)).json()["top_users"]
    (after,) = [r for r in rows if r["user_id"] == str(target)]
    assert after["disabled"] is True
    assert [r[:2] for r in audit(superuser, actor.user_id)] == [
        ("abuse.list", "ok"), ("user.disable", "ok"), ("abuse.list", "ok")
    ]  # fmt: skip
    # An admin is listed with its role, and the route refuses to disable one.
    boss = await env.user("boss@example.com", role="admin")
    plant(superuser, boss, session=uuid.uuid4(), net=net(), count=9)
    refused = await actor.post(f"/api/admin/users/{boss}/disable")
    assert (refused.status_code, reason(refused)) == (409, "target_is_admin")


def test_the_view_reads_the_network_hash_only_inside_a_distinct_count() -> None:
    """maint may read ``security_log.ip_hash`` (guard 11's one hash) only to count networks: every
    SQL string of the module that names it does so as ``count(DISTINCT ip_hash)``, and no output
    model has a field that could hold it."""
    source = (SRC / "admin" / "abuse.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    tree.body = tree.body[1:]  # the module docstring talks about the hash; it is not SQL
    strings = [n.value for n in ast.walk(tree) if isinstance(n, ast.Constant)
               and isinstance(n.value, str)]  # fmt: skip
    sql = " ".join(s for s in strings if "FROM security_log" in s or "ip_hash" in s)
    assert "count(DISTINCT ip_hash)" in sql
    assert sql.count("ip_hash") == sql.count("count(DISTINCT ip_hash)")
    assert not re.search(r"\bSELECT\s+\*|\bmethod\b|\broute\b", sql)
    fields = {f for model in (abuse.TopUser, abuse.LimitHit, abuse.MovedSession, abuse.AbuseOut)
              for f in model.model_fields}  # fmt: skip
    assert not [f for f in fields if re.search(r"hash|ip|addr|agent|token|route|path", f)]


def _admin_path_files() -> list[Path]:
    """Every module an admin route runs on the maint session: the admin package, the account
    helpers written for it, and the connection status it calls."""
    files = [*(SRC / "admin").rglob("*.py"), *(SRC / "auth").glob("admin_*.py")]
    files += (SRC / "auth").glob("connections*.py")
    return sorted(f for f in files if f.name != "abuse.py" or f.parent.name != "admin")


def test_no_other_admin_code_reads_the_security_log() -> None:
    """The grant lets maint read ``security_log.ip_hash``, so the place is pinned as well as the
    form: only ``admin/abuse.py`` names the table, and no other string on the admin path that
    names ``ip_hash`` is a query (the audit insert writes ``admin_audit.ip_hash``; it reads
    nothing)."""
    files = _admin_path_files()
    assert SRC / "admin" / "audit_routes.py" in files  # the glob found the package
    for path in files:
        source = path.read_text(encoding="utf-8")
        assert not re.search(r"security_?log", source, re.IGNORECASE), path.name
        tree = ast.parse(source)
        tree.body = tree.body[1:] if ast.get_docstring(tree) else tree.body
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Constant)
                and isinstance(node.value, str)
                and "ip_hash" in node.value
            ):
                assert not re.search(
                    r"\b(SELECT|FROM|JOIN|RETURNING)\b", node.value, re.IGNORECASE
                ), (path.name, node.value[:60])
