"""Who may register a system-write table (#1017, from the #1014 review, N1): a guard, no database.

``db.system_statement`` writes only the ``Table`` objects registered in
``db.system_write_tables``. The registry is first-come: any module could register a system table
name nobody has claimed yet. Today only ``classbeat.auth.limit_writes`` registers, and only
``rate_limits``; a new registration has to change this test and so go through review.

Two checks: (1) a fresh interpreter that imports the web app factory and the worker entry point
ends with exactly ``{"rate_limits"}`` registered, and that entry is the real table object; (2) an
AST scan: every spelling of ``register_system_write_table`` (a call, an import, an alias, an
attribute, a string for ``getattr``) lives only in the defining module, the ``classbeat.db``
re-export and ``limit_writes``, which calls it once, for ``rate_limits``.

Limit: a name built at run time (concatenation, ``vars``) is outside any static check; the
subprocess check still sees what it registers."""

import ast
import json
import subprocess
import sys

from test_billing_guard import modules

NAME = "register_system_write_table"
DEFINER = "classbeat.db.system_write_tables"
REEXPORT = "classbeat.db.__init__"  # modules() names a package by its __init__ file
LIMITER = "classbeat.auth.limit_writes"

PROBE = """
import json
import classbeat.web
import classbeat.worker.__main__
from classbeat.auth.tables import rate_limits
from classbeat.db.system_write_tables import system_write_tables

registry = system_write_tables()
print(json.dumps({"names": sorted(registry), "real": registry.get("rate_limits") is rate_limits}))
"""


def test_the_app_and_the_worker_register_exactly_the_real_rate_limits() -> None:
    run = subprocess.run(  # noqa: S603 - our own interpreter, a fixed probe
        [sys.executable, "-c", PROBE], capture_output=True, text=True, timeout=120, check=False
    )
    assert run.returncode == 0, run.stderr[-500:]
    assert json.loads(run.stdout.splitlines()[-1]) == {"names": ["rate_limits"], "real": True}


def _spellings(tree: ast.AST) -> list[ast.AST]:
    """Every node that spells the function: a name, an attribute, an import (also aliased) or a
    string (``getattr(module, "register_system_write_table")``, ``__all__``)."""
    found: list[ast.AST] = []
    for node in ast.walk(tree):
        if (
            (isinstance(node, ast.Name) and node.id == NAME)
            or (isinstance(node, ast.Attribute) and node.attr == NAME)
            or (isinstance(node, ast.alias) and node.name.split(".")[-1] == NAME)
            or (isinstance(node, ast.Constant) and node.value == NAME)
            or (isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and node.name == NAME)
        ):
            found.append(node)
    return found


def test_the_function_is_spelled_only_in_the_registry_its_reexport_and_the_limiter() -> None:
    spelled = {m for m, tree in modules().items() if _spellings(tree)}
    assert spelled == {DEFINER, REEXPORT, LIMITER}


def test_the_limiter_calls_it_once_with_rate_limits() -> None:
    calls = [
        node
        for node in ast.walk(modules()[LIMITER])
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == NAME
    ]
    assert len(calls) == 1
    assert [ast.unparse(a) for a in calls[0].args] == ["rate_limits"]
    assert not calls[0].keywords
    # the same count of spellings: the import and the call, nothing aliased or stringified
    assert len(_spellings(modules()[LIMITER])) == 2
