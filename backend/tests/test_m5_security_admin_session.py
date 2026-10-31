"""M5 security suite, part 2 (#227): the admin session's edges (PR #277 review).

- A revoke through another user's path never touches a live entitlement (the owner check).
- An integrity error in ``admin_session`` other than the target-user foreign key stays an
  ``error``, never the 404 ``not_found`` that only an unknown target user gets.
- A body that is not JSON (refused by FastAPI before any dependency) meets the admin gate first:
  a non-admin gets the same 403 as with a valid body, and an admin's 422 has its row.
- Guard 11 (no NOTIFY, LISTEN or advisory lock) reaches past ``classbeat/admin/``: every module
  defining a ``classbeat`` name the admin package uses (what runs inside ``admin_session``,
  such as ``auth.admin_accounts`` and ``connections.status``) is scanned too.
"""

import ast
import importlib
import inspect
import secrets
import uuid
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.routing import APIRoute
from sqlalchemy import text
from sqlalchemy.engine import Connection, make_url
from sqlalchemy.exc import IntegrityError

from admintools import audit_rows, new_admin
from authtools import ORIGIN, Env, running_env, session_cookie
from classbeat.admin.audit import Target
from classbeat.admin.db import AdminRefused, admin_session
from classbeat.admin.routes import admin_area_routes
from maint_guard import forbidden_calls

SRC = Path(__file__).resolve().parents[1] / "src" / "classbeat"


@pytest.fixture
async def env(app_database_url: str, maint_database_url: str) -> AsyncIterator[Env]:
    url = make_url(maint_database_url)
    async with running_env(
        app_database_url,
        maint_database_url=url.set(password=None).render_as_string(hide_password=False),
        database_maint_password=url.password,
    ) as env:
        yield env


def live_entitlement(conn: Connection, owner: object) -> uuid.UUID:
    return conn.execute(
        text(
            "INSERT INTO entitlements (owner_id, plan_id, source) VALUES (:o, 'pro', 'grant') "
            "RETURNING id"
        ),
        {"o": owner},
    ).scalar_one()


async def test_revoking_another_users_live_entitlement_is_404_and_it_stays_live(
    env: Env, make_actor: Any, superuser: Connection
) -> None:
    """PR #277 review: the old test revoked an already-revoked entitlement through the wrong
    user, which a missing owner check would also have answered with a refusal."""
    admin_id = await env.user("a@example.com", role="admin")
    actor = await make_actor(admin_id, mfa=True)
    await env.run("UPDATE sessions SET stepup_at = now() WHERE user_id = :u", u=admin_id)
    alice, bob = await env.user("alice@example.com"), await env.user("bob@example.com")
    alices = live_entitlement(superuser, alice)
    response = await actor.post(f"/api/admin/users/{bob}/entitlements/{alices}/revoke")
    assert (response.status_code, response.json()["detail"]["reason"]) == (404, "not_found")
    kept = superuser.execute(
        text("SELECT revoked_at, revoked_by FROM entitlements WHERE id = :e"), {"e": alices}
    ).one()
    assert tuple(kept) == (None, None)
    rider = await make_actor(alice)
    assert (await rider.get("/api/account/plan")).json()["plan"] == "pro"
    # The right path still revokes it: the 404 came from the owner check, not a broken route.
    done = await actor.post(f"/api/admin/users/{alice}/entitlements/{alices}/revoke")
    assert done.status_code == 200, done.text


async def test_another_integrity_error_inside_the_session_stays_an_error(
    env: Env, superuser: Connection
) -> None:
    """A foreign key of another table, raised by the action itself, is an ``error`` row and
    the original exception, not ``AdminRefused`` 404."""
    db = env.app.state.admin_db
    actor = new_admin(superuser)
    target = await env.user("t@example.com")
    with pytest.raises(IntegrityError):
        async with admin_session(
            db, actor, "user.grant", write=True, target=Target(user_id=target),
            params={"plan_id": "pro"},
        ) as session:  # fmt: skip
            await session.execute(
                text(
                    "INSERT INTO entitlements (owner_id, plan_id, source) "
                    "VALUES (:o, 'pro', 'grant')"
                ),
                {"o": uuid.uuid4()},  # no such user: entitlements' own foreign key
            )
    assert audit_rows(superuser, actor) == [("error", "internal")]


async def test_an_integrity_error_of_the_audit_row_itself_is_not_a_404(
    env: Env, superuser: Connection, caplog: pytest.LogCaptureFixture
) -> None:
    """Only ``admin_audit_target_user_id_fkey`` means "no such user". Another constraint of
    the audit row (a target kind its check refuses), with an unknown target user, re-raises
    instead of being taken for a missing user."""
    db = env.app.state.admin_db
    actor = new_admin(superuser)
    target = Target(user_id=uuid.uuid4(), kind="Not A Kind")
    with pytest.raises(IntegrityError) as raised:
        async with admin_session(db, actor, "user.read", target=target):
            pass
    assert not isinstance(raised.value, AdminRefused)
    # Its error row cannot be written either (the same check refuses it): logged, not hidden.
    assert audit_rows(superuser, actor) == []
    assert "admin audit row not written" in caplog.text


async def test_an_unknown_target_user_alone_is_the_404(env: Env, superuser: Connection) -> None:
    db = env.app.state.admin_db
    actor = new_admin(superuser)
    with pytest.raises(AdminRefused) as raised:
        async with admin_session(db, actor, "user.read", target=Target(user_id=uuid.uuid4())):
            pass
    assert (raised.value.status, raised.value.reason) == (404, "not_found")
    assert audit_rows(superuser, actor) == [("refused", "not_found")]


# --- a body that is not JSON (the #303 review's note 4) ----------------------------------------


async def send_raw(actor: Any, method: str, path: str, content: bytes) -> Any:
    """``actor.call`` with a raw body, which FastAPI refuses before any dependency runs."""
    info = await actor.client.get("/api/auth/session", headers=session_cookie(actor.token))
    headers = {
        "Origin": ORIGIN,
        "X-CSRF-Token": info.json()["csrf_token"],
        "Content-Type": "application/json",
        **session_cookie(actor.token),
    }
    response = await actor.client.request(method, path, headers=headers, content=content)
    actor.client.cookies.clear()
    return response


def body_routes(env: Env) -> list[tuple[str, str]]:
    found = [
        (method, route.path)
        for path, route in admin_area_routes(env.app.routes)
        if isinstance(route, APIRoute) and route.dependant.body_params
        for method in sorted(route.methods or ())
    ]
    assert len(found) >= 2  # the grant and the delete, at least
    return found


async def test_a_body_that_is_not_json_meets_the_gate_first(
    env: Env, make_actor: Any, superuser: Connection
) -> None:
    """FastAPI refuses a body that is not JSON before any dependency, so without care a non-admin
    would get a 422 (learning the route takes a JSON body) where a valid body gets the 403, and an
    admin's refusal would leave no row. The gate runs first: a non-admin's answer is the same 403
    whatever the body, an admin without step-up gets ``stepup_required`` with its row, and a
    stepped-up admin gets the 422 with one ``invalid_input`` row of the route's template."""
    plain_id = await env.user("plain@example.com")
    plain = await make_actor(plain_id, mfa=True)
    admin_id = await env.user("a@example.com", role="admin")
    no_stepup = await make_actor(admin_id, mfa=True)
    stepped_id = await env.user("b@example.com", role="admin")
    stepped = await make_actor(stepped_id, mfa=True)
    await env.run("UPDATE sessions SET stepup_at = now() WHERE user_id IN (:p, :s)",
                  p=plain_id, s=stepped_id)  # fmt: skip
    garbage = b'{"plan_id": "pro",'  # cut off: not JSON
    for method, template in body_routes(env):
        path = template.replace("{user_id}", str(uuid.uuid4()))
        valid = await plain.call(method, path, {"plan_id": "pro", "email": "x@example.com"})
        broken = await send_raw(plain, method, path, garbage)
        assert (broken.status_code, broken.json()) == (valid.status_code, valid.json()), path
        assert broken.json() == {"detail": {"reason": "forbidden"}}
        refused = await send_raw(no_stepup, method, path, garbage)
        assert (refused.status_code, refused.json()["detail"]["reason"]) == (
            403,
            "stepup_required",
        ), path
        invalid = await send_raw(stepped, method, path, garbage)
        assert invalid.status_code == 422, path
        assert invalid.headers["cache-control"] == "no-store"
        latest = superuser.execute(
            text(
                "SELECT action, outcome, reason, params FROM admin_audit WHERE actor_id = :a "
                "ORDER BY created_at DESC, id DESC LIMIT 1"
            ),
            {"a": stepped_id},
        ).one()
        assert tuple(latest) == (
            "admin.request", "refused", "invalid_input", {"method": method, "route": template}
        )  # fmt: skip
    routes = len(body_routes(env))
    assert audit_of(superuser, plain_id) == []
    assert audit_of(superuser, admin_id) == [("refused", "stepup_required")] * routes
    assert audit_of(superuser, stepped_id) == [("refused", "invalid_input")] * routes


def audit_of(conn: Connection, actor_id: object) -> list[tuple[str, str | None]]:
    return [
        (r.outcome, r.reason)
        for r in conn.execute(
            text(
                "SELECT outcome, reason FROM admin_audit WHERE actor_id = :a "
                "ORDER BY created_at, id"
            ),
            {"a": actor_id},
        )
    ]


# --- guard 11 past the admin package ----------------------------------------------------------

# A name that matches the guard's pattern without being SQL: SQLAlchemy's ``event.listen``
# (``classbeat.db.session``'s transaction guard), not ``LISTEN``. Pinned to its one line.
NOT_SQL = {("classbeat.db.session", "listen"): "event.listen(session.sync_session"}


def used_modules(path: Path, prefix: str = "classbeat") -> set[str]:
    """The ``classbeat`` modules that define what ``path`` imports or uses: ``from x import
    y`` resolves to ``y``'s own module, and ``auth.y`` (a package re-export) to the module
    that defines ``y``."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    aliases: dict[str, str] = {}
    found: set[str] = set()

    def owner(obj: Any, fallback: str) -> str:
        module = obj if inspect.ismodule(obj) else inspect.getmodule(obj)
        return module.__name__ if module is not None else fallback

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            aliases |= {a.asname or a.name: a.name for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            module = importlib.import_module(node.module)
            for alias in node.names:
                obj = getattr(module, alias.name, None)
                if inspect.ismodule(obj):
                    aliases[alias.asname or alias.name] = obj.__name__
                found.add(owner(obj, node.module) if obj is not None else node.module)
        elif isinstance(node, ast.ImportFrom) and node.level:
            raise AssertionError(f"{path}: a relative import; spell it out for this guard")
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
            name = aliases.get(node.value.id)
            if name and name.startswith(prefix):
                obj = getattr(importlib.import_module(name), node.attr, None)
                if obj is not None:
                    found.add(owner(obj, name))
    return {m for m in found if m == prefix or m.startswith(prefix + ".")}


def reached() -> dict[str, Path]:
    modules: set[str] = set()
    for path in sorted((SRC / "admin").rglob("*.py")):
        modules |= used_modules(path)
    return {m: Path(inspect.getfile(importlib.import_module(m))) for m in sorted(modules)}


def test_guard_11_scans_every_module_the_admin_package_reaches() -> None:
    modules = reached()
    # The PR #277 review's examples, and the gate's own helpers.
    assert {
        "classbeat.auth.admin_accounts",
        "classbeat.connections.status",
        "classbeat.auth.ratelimit",
    } <= set(modules)
    found = []
    for module, path in modules.items():
        source = path.read_text(encoding="utf-8")
        lines = source.splitlines()
        for line, what in forbidden_calls(source):
            allowed = NOT_SQL.get((module, what.lower()))
            if allowed is not None and allowed in lines[line - 1]:
                continue
            found.append(f"{module}:{line}: {what}")
    assert found == []


def test_guard_11_reach_catches_a_lock_in_a_used_module(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The scan above is not vacuous: a used module that takes an advisory lock is found."""
    package = tmp_path / f"cbprobe_{secrets.token_hex(4)}"
    package.mkdir()
    (package / "__init__.py").write_text("from .locks import grab\n", encoding="utf-8")
    (package / "locks.py").write_text(
        "def grab(db):\n    return db.execute('SELECT pg_advisory_xact_lock(1)')\n",
        encoding="utf-8",
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    user = tmp_path / "user.py"
    user.write_text(f"import {package.name} as p\n\ndef f(db):\n    p.grab(db)\n", "utf-8")
    found = used_modules(user, prefix=package.name)
    assert found == {f"{package.name}.locks"}  # the defining module, not the re-exporting package
    source = Path(inspect.getfile(importlib.import_module(f"{package.name}.locks"))).read_text()
    assert [what for _, what in forbidden_calls(source)] == ["pg_advisory_xact_lock"]
