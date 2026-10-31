"""M5 security suite (#227): guard 11 (no NOTIFY, LISTEN or advisory lock in what runs on the
admin path, ADR 0011 section 5) followed through the whole call chain (#303 review, note 3).

``test_maint_privileges.py`` scans ``classbeat/admin/`` itself. This scan starts there and
follows every ``classbeat`` name the code uses to what defines it, then every name *that* code
uses, to the end of the chain: functions and classes by their own source (``inspect``), module
constants (a ``text(...)`` statement, say) by their assignment, a name imported from elsewhere to
the module that defines it. It follows what the code names, not whole modules: a module import
would drag in the whole app (``admin.usage`` imports two date helpers from ``usage.service``,
whose module imports the job kinds and so the NOTIFY of ``jobs.events``), which no admin route
runs.

What it cannot see is a call made through a value (a callback passed in, ``getattr`` with a
computed name); the admin package uses neither for database work.
"""

import ast
import importlib
import inspect
import secrets
import sys
import textwrap
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from maint_guard import forbidden_calls

SRC = Path(__file__).resolve().parents[1] / "src" / "classbeat"

# A name that matches the guard's pattern without being SQL: SQLAlchemy's ``event.listen`` in
# the transaction guard (``TransactionGuard`` is ``_Guard``). Pinned to its object and to the
# line's text.
NOT_SQL = {("classbeat.db.session._Guard", "listen"): "event.listen(session.sync_session"}


def _module_tree(module: ModuleType) -> ast.Module:
    return ast.parse(Path(inspect.getfile(module)).read_text(encoding="utf-8"))


def _assignment(module: ModuleType, name: str) -> tuple[str, ast.AST] | None:
    """The source of ``name``'s top-level assignment in ``module``, or where it is imported
    from as ``("import", (module name, attribute))`` encoded in the second item."""
    tree = _module_tree(module)
    source = Path(inspect.getfile(module)).read_text(encoding="utf-8")
    for node in tree.body:
        targets: list[ast.expr] = []
        if isinstance(node, ast.Assign):
            targets = list(node.targets)
        elif isinstance(node, ast.AnnAssign):
            targets = [node.target]
        if any(isinstance(t, ast.Name) and t.id == name for t in targets):
            return ast.get_source_segment(source, node) or "", node
    return None


def _imported_from(module: ModuleType, name: str) -> tuple[str, str] | None:
    for node in _module_tree(module).body:
        if isinstance(node, ast.ImportFrom) and node.module and not node.level:
            for alias in node.names:
                if (alias.asname or alias.name) == name:
                    return node.module, alias.name
    return None


class Item:
    """One thing on the chain: its qualified name, its source, and the namespace its names
    resolve in."""

    def __init__(self, key: str, source: str, namespace: dict[str, Any], module: ModuleType):
        self.key, self.source, self.namespace, self.module = key, source, namespace, module


def _names(source: str) -> Iterator[tuple[str, str | None]]:
    """``(name, attribute)`` pairs: ``x`` as ``("x", None)``, ``x.y`` as ``("x", "y")``."""
    tree = ast.parse(textwrap.dedent(source))
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
            yield node.value.id, node.attr
        elif isinstance(node, ast.Name):
            yield node.id, None


def _within(name: str, prefix: str) -> bool:
    return name == prefix or name.startswith(prefix + ".")


def _item_for(obj: Any, prefix: str) -> Item | None:
    obj = inspect.unwrap(obj) if callable(obj) else obj
    if inspect.ismodule(obj):
        return None  # a module itself is only a namespace; its used names are followed
    module = inspect.getmodule(obj)
    if module is None or not (
        module.__name__ == prefix or module.__name__.startswith(prefix + ".")
    ):
        return None
    if not (inspect.isfunction(obj) or inspect.isclass(obj)):
        return None
    try:
        source = inspect.getsource(obj)
    except (OSError, TypeError):
        return None
    return Item(f"{module.__name__}.{obj.__qualname__}", source, vars(module), module)


def _constant_item(module: ModuleType, name: str, prefix: str, depth: int = 0) -> Item | None:
    """A module constant: its assignment's source, or (imported) the defining module's."""
    if depth > 10 or not module.__name__.startswith(prefix):
        return None
    found = _assignment(module, name)
    if found is not None:
        return Item(f"{module.__name__}.{name}", found[0], vars(module), module)
    origin = _imported_from(module, name)
    if origin is None or not origin[0].startswith(prefix):
        return None
    return _constant_item(importlib.import_module(origin[0]), origin[1], prefix, depth + 1)


def _resolve(item: Item, name: str, attr: str | None, prefix: str) -> Item | None:
    value = item.namespace.get(name)
    if value is None:
        return None
    if inspect.ismodule(value):
        if attr is None or not value.__name__.startswith(prefix):
            return None
        inner = getattr(value, attr, None)
        if inner is None:
            return None
        resolved = _item_for(inner, prefix)
        if resolved is not None or inspect.ismodule(inner) or callable(inner):
            return resolved
        return _constant_item(value, attr, prefix)
    resolved = _item_for(value, prefix)
    if resolved is not None:
        return resolved
    if callable(value) or inspect.isclass(value):
        return None  # third-party or built-in
    return _constant_item(item.module, name, prefix)


def reach(
    roots: list[ModuleType], prefix: str = "classbeat", values: tuple[Any, ...] = ()
) -> dict[str, Item]:
    """Every function, class and module constant the ``roots`` modules reach by name, plus the
    classes in ``values`` (objects the path calls through a value, not a name)."""
    found: dict[str, Item] = {}
    todo = [
        Item(m.__name__, Path(inspect.getfile(m)).read_text(encoding="utf-8"), vars(m), m)
        for m in roots
    ]
    todo += [i for v in values if (i := _item_for(v, prefix)) is not None]
    while todo:
        item = todo.pop()
        if item.key in found:
            continue
        found[item.key] = item
        for name, attr in set(_names(item.source)):
            nxt = _resolve(item, name, attr, prefix)
            if nxt is not None and nxt.key not in found:
                todo.append(nxt)
    return dict(sorted(found.items()))


# What the admin path calls through a value rather than a name: the gate's
# ``request.app.state.limiter.begin`` (the auth rate limiter).
VALUE_ROOTS: tuple[Any, ...] = (importlib.import_module("classbeat.auth.ratelimit").RateLimiter,)


def admin_modules() -> list[ModuleType]:
    return [
        importlib.import_module("classbeat.admin" + ("" if p.stem == "__init__" else f".{p.stem}"))
        for p in sorted((SRC / "admin").glob("*.py"))
    ]


def problems(items: dict[str, Item]) -> list[str]:
    found: list[str] = []
    for key, item in items.items():
        source = textwrap.dedent(item.source)
        lines = source.splitlines()
        for line, what in forbidden_calls(source):
            pinned = NOT_SQL.get((key, what.lower()))
            if pinned is not None and 0 < line <= len(lines) and pinned in lines[line - 1]:
                continue
            found.append(f"{key}:{line}: {what}")
    return found


def test_guard_11_follows_every_name_the_admin_path_reaches() -> None:
    items = reach(admin_modules(), values=VALUE_ROOTS)
    keys = set(items)
    # The PR #277 review's examples, the gate's helpers, and two and three levels down.
    for expected in (
        "classbeat.auth.admin_accounts.disable_account",
        "classbeat.connections.status.connection_statuses",
        "classbeat.auth.ratelimit.RateLimiter",
        "classbeat.auth.accounts.disable_invite",
        "classbeat.db.session._Guard",
    ):
        assert any(k == expected or k.startswith(expected + ".") for k in keys), expected
    assert not any(k.startswith("classbeat.jobs.events") for k in keys)  # not on the admin path
    assert problems(items) == []


@pytest.fixture
def probe(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    name = f"cbprobe_{secrets.token_hex(4)}"
    package = tmp_path / name
    package.mkdir()
    (package / "__init__.py").write_text("from .helpers import tidy as tidy\n", encoding="utf-8")
    (package / "deep.py").write_text(
        "from sqlalchemy import text\n\n"
        "_LOCK = text('SELECT pg_advisory_xact_lock(7)')\n\n\n"
        "def lock(db):\n    return db.execute(_LOCK)\n",
        encoding="utf-8",
    )
    (package / "helpers.py").write_text(
        f"from {name}.deep import lock\n\n\ndef tidy(db):\n    return lock(db)\n\n\n"
        "def unused(db):\n    return db.execute('NOTIFY x')\n",
        encoding="utf-8",
    )
    (package / "entry.py").write_text(
        f"import {name} as p\n\n\nasync def route(db):\n    return p.tidy(db)\n",
        encoding="utf-8",
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    yield name
    for mod in [m for m in sys.modules if m == name or m.startswith(name + ".")]:
        del sys.modules[mod]


def test_the_reach_follows_a_lock_three_levels_down_through_a_constant(probe: str) -> None:
    """Not vacuous: ``entry.route`` uses the package's re-export ``tidy``, which calls
    ``deep.lock``, which runs the module constant ``_LOCK``. The lock is found; ``unused``, which
    nothing on the chain names, is not followed."""
    items = reach([importlib.import_module(f"{probe}.entry")], prefix=probe)
    assert {f"{probe}.helpers.tidy", f"{probe}.deep.lock", f"{probe}.deep._LOCK"} <= set(items)
    assert f"{probe}.helpers.unused" not in items
    assert [p.split(": ")[1] for p in problems(items)] == ["pg_advisory_xact_lock"]
