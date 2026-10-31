"""M5 security suite, part 2 (#227): the admin session's edges (PR #277 review).

- A revoke through another user's path never touches a live entitlement (the owner check).
- An integrity error in ``admin_session`` other than the target-user foreign key stays an
  ``error``, never the 404 ``not_found`` that only an unknown target user gets.
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
from sqlalchemy import text
from sqlalchemy.engine import Connection, make_url
from sqlalchemy.exc import IntegrityError

from admintools import audit_rows, new_admin
from authtools import Env, running_env
from classbeat.admin.audit import Target
from classbeat.admin.db import AdminRefused, admin_session
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
