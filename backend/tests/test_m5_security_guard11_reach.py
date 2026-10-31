"""M5 security suite (#227): guard 11 (no NOTIFY, LISTEN or advisory lock in what runs on the
admin path, ADR 0011 section 5) followed through the whole call chain (#303 review, note 3).

``test_maint_privileges.py`` scans ``classbeat/admin/`` itself. This scan starts there and
follows every ``classbeat`` name the code uses to what defines it, then every name *that* code
uses, to the end of the chain:

- functions and classes by their own source (``inspect``), module constants (a ``text(...)``
  statement, say) by their assignment;
- a name imported at module level, or inside a function body (``from x import f`` in a
  function), relative imports included, to the module that defines it;
- an attribute chain through modules (``jobs.events.notify_event(...)`` after
  ``from classbeat import jobs``) to the object at its end.

It follows what the code names, not whole modules: a module import would drag in the whole app
(``admin.usage`` imports two date helpers from ``usage.service``, whose module imports the job
kinds and so the NOTIFY of ``jobs.events``), which no admin route runs.

What it cannot see is a call made through a value (a callback passed in, ``getattr`` with a
computed name, ``importlib``); the admin package uses none of them for database work, and
``VALUE_ROOTS`` names the one object the gate calls through ``request.app.state``.
"""

import ast
import importlib
import importlib.util
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


def _source_of(module: ModuleType) -> str:
    return Path(inspect.getfile(module)).read_text(encoding="utf-8")


def _absolute(module: ModuleType, name: str | None, level: int) -> str:
    """The absolute module name of ``from <level dots><name> import ...`` written in ``module``."""
    if not level:
        return name or ""
    package = module.__name__ if hasattr(module, "__path__") else module.__package__ or ""
    return importlib.util.resolve_name("." * level + (name or ""), package)


def _assignment(module: ModuleType, name: str) -> str | None:
    """The source of ``name``'s top-level assignment in ``module``."""
    source = _source_of(module)
    for node in ast.parse(source).body:
        targets: list[ast.expr] = []
        if isinstance(node, ast.Assign):
            targets = list(node.targets)
        elif isinstance(node, ast.AnnAssign):
            targets = [node.target]
        if any(isinstance(t, ast.Name) and t.id == name for t in targets):
            return ast.get_source_segment(source, node) or ""
    return None


def _imported_from(module: ModuleType, name: str) -> tuple[str, str] | None:
    for node in ast.parse(_source_of(module)).body:
        if isinstance(node, ast.ImportFrom):
            for alias in node.names:
                if (alias.asname or alias.name) == name:
                    return _absolute(module, node.module, node.level), alias.name
    return None


class Item:
    """One thing on the chain: its qualified name, its source, and the namespace its names
    resolve in."""

    def __init__(self, key: str, source: str, namespace: dict[str, Any], module: ModuleType):
        self.key, self.source, self.namespace, self.module = key, source, namespace, module


def _within(name: str, prefix: str) -> bool:
    return name == prefix or name.startswith(prefix + ".")


def _chains(tree: ast.AST) -> Iterator[tuple[str, tuple[str, ...]]]:
    """Every name and every attribute chain on a name: ``a.b.c`` as ``("a", ("b", "c"))`` (and
    its prefixes), ``x`` as ``("x", ())``."""
    for node in ast.walk(tree):
        attrs: list[str] = []
        current: ast.expr = node if isinstance(node, ast.expr) else ast.Constant(None)
        while isinstance(current, ast.Attribute):
            attrs.append(current.attr)
            current = current.value
        if isinstance(current, ast.Name):
            yield current.id, tuple(reversed(attrs))


def _import_module(name: str) -> ModuleType | None:
    try:
        return importlib.import_module(name)
    except ImportError:
        return None


def _attribute(module: ModuleType, attr: str) -> Any:
    """``module.attr``, importing ``module.attr`` as a submodule when it is not set yet."""
    value = getattr(module, attr, None)
    return value if value is not None else _import_module(f"{module.__name__}.{attr}")


# A binding: the value a name holds, and where a constant's assignment is to be found
# (the module it lives in and its name there).
Binding = tuple[Any, ModuleType, str]


def _bindings(item: Item, tree: ast.AST) -> dict[str, Binding]:
    """The item's own names: its module's globals, and what the imports inside its source
    bind (a function body's ``from x import f``, relative ones included)."""
    found: dict[str, Binding] = {k: (v, item.module, k) for k, v in item.namespace.items()}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            base = _import_module(_absolute(item.module, node.module, node.level))
            if base is None:
                continue
            for alias in node.names:
                if alias.name != "*":
                    value = _attribute(base, alias.name)
                    if value is not None:
                        found[alias.asname or alias.name] = (value, base, alias.name)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.asname:
                    module = _import_module(alias.name)
                    bound = alias.asname
                else:
                    bound = alias.name.split(".")[0]
                    module = _import_module(bound)
                if module is not None:
                    found[bound] = (module, module, bound)
    return found


def _item_for(obj: Any, prefix: str) -> Item | None:
    obj = inspect.unwrap(obj) if callable(obj) else obj
    if inspect.ismodule(obj) or not (inspect.isfunction(obj) or inspect.isclass(obj)):
        return None  # a module itself is only a namespace; its used names are followed
    module = inspect.getmodule(obj)
    if module is None or not _within(module.__name__, prefix):
        return None
    try:
        source = inspect.getsource(obj)
    except (OSError, TypeError):
        return None
    return Item(f"{module.__name__}.{obj.__qualname__}", source, vars(module), module)


def _constant_item(module: ModuleType, name: str, prefix: str, depth: int = 0) -> Item | None:
    """A module constant: its assignment's source, or (imported) the defining module's."""
    if depth > 10 or not _within(module.__name__, prefix):
        return None
    found = _assignment(module, name)
    if found is not None:
        return Item(f"{module.__name__}.{name}", found, vars(module), module)
    origin = _imported_from(module, name)
    if origin is None or not _within(origin[0], prefix):
        return None
    defining = _import_module(origin[0])
    if defining is None:
        return None
    return _constant_item(defining, origin[1], prefix, depth + 1)


def _resolve(binding: Binding, attrs: tuple[str, ...], prefix: str) -> Item | None:
    """The object at the end of ``name.attr1.attr2...``, walked through modules."""
    value, owner, owner_name = binding
    for attr in attrs:
        if not inspect.ismodule(value):
            break  # an attribute of a class or object: the class's own source is followed
        if not _within(value.__name__, prefix):
            return None
        owner, owner_name, value = value, attr, _attribute(value, attr)
        if value is None:
            return None
    if inspect.ismodule(value):
        return None
    resolved = _item_for(value, prefix)
    if resolved is not None or callable(value):
        return resolved  # a classbeat function or class, or third-party / built-in
    return _constant_item(owner, owner_name, prefix)


def reach(
    roots: list[ModuleType], prefix: str = "classbeat", values: tuple[Any, ...] = ()
) -> dict[str, Item]:
    """Every function, class and module constant the ``roots`` modules reach by name, plus the
    classes in ``values`` (objects the path calls through a value, not a name)."""
    found: dict[str, Item] = {}
    todo = [Item(m.__name__, _source_of(m), vars(m), m) for m in roots]
    todo += [i for v in values if (i := _item_for(v, prefix)) is not None]
    while todo:
        item = todo.pop()
        if item.key in found:
            continue
        found[item.key] = item
        tree = ast.parse(textwrap.dedent(item.source))
        names = _bindings(item, tree)
        for name, attrs in set(_chains(tree)):
            binding = names.get(name)
            nxt = None if binding is None else _resolve(binding, attrs, prefix)
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


def relative_imports(items: dict[str, Item]) -> list[str]:
    return [
        key
        for key, item in items.items()
        for node in ast.walk(ast.parse(textwrap.dedent(item.source)))
        if isinstance(node, ast.ImportFrom) and node.level
    ]


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
    assert problems(items) == []
    assert not any(k.startswith("classbeat.jobs.events") for k in keys)  # not on the admin path
    # The scan follows relative imports, but classbeat writes none on this path; one appearing
    # is worth a look here first (#315 review).
    assert relative_imports(items) == []


@pytest.fixture
def probe(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    """A package whose entry reaches a forbidden statement in every way the scan follows."""
    name = f"cbprobe_{secrets.token_hex(4)}"
    package = tmp_path / name
    package.mkdir()
    files = {
        "__init__.py": "from .helpers import tidy as tidy\n",
        "deep.py": """
            from sqlalchemy import text

            _LOCK = text('SELECT pg_advisory_xact_lock(7)')


            def lock(db):
                return db.execute(_LOCK)


            def local_target(db):
                return db.execute(text("SELECT pg_notify('g4', 'x')"))


            def chained(db):
                return db.execute(text('LISTEN g5'))


            def relative_target(db):
                return db.execute(text('NOTIFY rel'))


            def never(db):
                return db.execute(text('NOTIFY never'))
        """,
        "helpers.py": f"""
            from {name}.deep import lock


            def tidy(db):
                from {name}.deep import local_target  # G4: a local import one level down

                lock(db)
                return local_target(db)


            def local_entry(db):
                from .deep import relative_target  # a local relative import

                relative_target(db)
                return db.execute('SELECT pg_try_advisory_lock(3)')


            def unused(db):
                return db.execute('NOTIFY x')
        """,
        "entry.py": f"""
            import {name} as p
            import {name}.deep


            async def route(db):
                from {name}.helpers import local_entry  # G3: a local import in the route

                local_entry(db)
                {name}.deep.chained(db)  # G5: a call through a module attribute chain
                return p.tidy(db)
        """,
    }
    for file, body in files.items():
        (package / file).write_text(textwrap.dedent(body).lstrip(), encoding="utf-8")
    monkeypatch.syspath_prepend(str(tmp_path))
    yield name
    for mod in [m for m in sys.modules if m == name or m.startswith(name + ".")]:
        del sys.modules[mod]


def test_the_reach_follows_every_way_the_probe_reaches_a_forbidden_statement(probe: str) -> None:
    """Not vacuous: ``entry.route`` reaches a forbidden statement in each way (#303 review
    note 3, #315 review G1-G5):

    - the package's re-export ``tidy``, which calls ``deep.lock``, which runs the module
      constant ``_LOCK`` (three levels down, through a constant);
    - inside ``tidy``, a function-local import of ``deep.local_target`` (G4);
    - in the route itself, a function-local import of ``helpers.local_entry`` (G3), which
      imports ``deep.relative_target`` locally and relatively;
    - ``<probe>.deep.chained(...)``, an attribute chain through the package (G5).

    ``helpers.unused`` and ``deep.never``, which nothing on the chain names, are not followed."""
    items = reach([importlib.import_module(f"{probe}.entry")], prefix=probe)
    flagged = {p.split(":")[0] for p in problems(items)}
    assert flagged == {
        f"{probe}.deep._LOCK",
        f"{probe}.deep.local_target",
        f"{probe}.helpers.local_entry",
        f"{probe}.deep.relative_target",
        f"{probe}.deep.chained",
    }
    assert f"{probe}.helpers.unused" not in items
    assert f"{probe}.deep.never" not in items
    assert relative_imports(items) == [f"{probe}.helpers.local_entry"]
