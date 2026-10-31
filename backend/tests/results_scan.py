"""The writer guard of the enable gate (ADR 0012 section 5 (a); #211).

Over ``backend/src/classbeat`` only (the migration that creates the table and the tests that check
it are outside its scope):

- no module but ``classbeat.models.store`` mentions ``model_eval_results``: in a string that is
  not a docstring (SQL, a table name for ``Table(...)`` or ``text(...)``), a name or an attribute;
  ``classbeat.db.tables`` may hold the bare name as its ``SYSTEM_TABLES`` key, nothing longer;
- no module but ``classbeat.evals.suite_live`` (EV5, the live command's ``--record``) and the
  defining ``classbeat.models.store`` reaches ``classbeat.models.store.record_result``: an import
  of it (by any alias, also relative), or, in a module that imports the store module (``from
  classbeat.models import store``, ``import classbeat.models.store``, ``from classbeat import
  models``, relative forms), any name, attribute or string ``record_result``
  (``store.record_result``, ``getattr(store, "record_result")``). The unrelated
  ``classbeat.connections.record_result`` is not the writer and is not flagged.

Both allowlists are reviewed changes. Like the other guards this catches accidental use and makes
the rest visible in review; it cannot stop obfuscated code (a name assembled at run time).
"""

import ast

TABLE = "model_eval_results"
WRITER = "record_result"
TABLE_MODULES = ("classbeat.models.store",)
# ``SYSTEM_TABLES`` lists the table by its bare name (a registry key, not SQL): there the exact
# string is allowed, and nothing longer that contains it.
TABLE_NAME_MODULES = ("classbeat.db.tables",)
WRITER_MODULES = ("classbeat.models.store", "classbeat.evals.suite_live")
STORE = "classbeat.models.store"


def _docstrings(tree: ast.AST) -> set[int]:
    ids: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
            body = node.body
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
                ids.add(id(body[0].value))
    return ids


def _mentions(node: ast.AST, word: str, docstrings: set[int]) -> bool:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return id(node) not in docstrings and word in node.value
    if isinstance(node, ast.Name):
        return node.id == word
    if isinstance(node, ast.Attribute):
        return node.attr == word
    if isinstance(node, ast.alias):
        return word in (node.name.rsplit(".", 1)[-1], node.asname)
    if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
        return node.name == word
    if isinstance(node, ast.arg):
        return node.arg == word
    if isinstance(node, ast.keyword):
        return node.arg == word
    return False


def _absolute(node: ast.ImportFrom, module: str, is_package: bool) -> str:
    """The module an ``ImportFrom`` names, relative imports resolved against ``module`` (a
    package's ``__init__`` is the package itself, so one level up is less)."""
    if not node.level:
        return node.module or ""
    parts = module.split(".")
    up = node.level - 1 if is_package else node.level
    base = parts[: len(parts) - up]
    return ".".join([*base, *([node.module] if node.module else [])])


def _store_imports(tree: ast.AST, module: str, is_package: bool) -> tuple[bool, list[int]]:
    """Whether the module imports the store module, and the lines importing the writer itself."""
    reaches, writer_lines = False, []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            if any(a.name in (STORE, "classbeat.models") or a.name.startswith(STORE + ".")
                   for a in node.names):  # fmt: skip
                reaches = True
        elif isinstance(node, ast.ImportFrom):
            source = _absolute(node, module, is_package)
            for a in node.names:
                full = f"{source}.{a.name}"
                if full == f"{STORE}.{WRITER}" or (source == STORE and a.name == "*"):
                    writer_lines.append(node.lineno)
                if full in (STORE, "classbeat.models") or source == STORE:
                    reaches = True
    return reaches, writer_lines


def scan_source(source: str, module: str, is_package: bool = False) -> list[tuple[int, str]]:
    """(line, reason) for each forbidden mention in ``module``'s ``source`` (``is_package``: the
    source is the package's ``__init__``)."""
    tree = ast.parse(source)
    docstrings = _docstrings(tree)
    found: list[tuple[int, str]] = []
    reaches_store, writer_imports = _store_imports(tree, module, is_package)
    if module not in WRITER_MODULES:
        found += [(n, f"imports {WRITER} (only classbeat.evals.suite_live may)")
                  for n in writer_imports]  # fmt: skip
    for node in ast.walk(tree):
        line = getattr(node, "lineno", 0)
        bare_name = isinstance(node, ast.Constant) and node.value == TABLE
        if module in TABLE_NAME_MODULES and bare_name:
            pass
        elif module not in TABLE_MODULES and _mentions(node, TABLE, docstrings):
            found.append((line, f"mentions {TABLE} (only classbeat.models.store may)"))
        if (
            module not in WRITER_MODULES
            and reaches_store
            and _mentions(node, WRITER, docstrings)
            and not isinstance(node, ast.alias)  # the import itself is reported above
        ):
            found.append((line, f"refers to {WRITER} (only classbeat.evals.suite_live may)"))
    return found
