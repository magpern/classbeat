"""What ``app.state.end_account_jobs`` is and who uses it (ADR 0014 section 3; #349 review).

The operator's delete of an account ends the target's jobs through this value, so that the admin
package does not import the jobs code (guard 11, ``test_m5_security_guard11_reach.py``, cannot
follow a call made through a value). Because the guard cannot see it, this file pins it: what the
value is, that the web app installs it before serving, and that only ``classbeat.web`` sets it and
only ``classbeat.admin.users`` calls it. The work runs in the target's own user sessions, on the
app engine, never on the maint session.
"""

import ast
from pathlib import Path

from authtools import Env
from classbeat.account import erase

SRC = Path(__file__).resolve().parent.parent / "src" / "classbeat"
NAME = "end_account_jobs"


def modules_naming(name: str) -> dict[str, list[str]]:
    """Source module -> how it names ``name`` (``set``: assigned to an attribute, ``use``: any
    other mention as an attribute or a name)."""
    found: dict[str, list[str]] = {}
    for path in sorted(SRC.rglob("*.py")):
        tree = ast.parse(path.read_text("utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and node.attr == name:
                kind = "set" if isinstance(node.ctx, ast.Store) else "use"
            elif isinstance(node, ast.Name) and node.id == name:
                kind = "use"
            else:
                continue
            found.setdefault(path.relative_to(SRC).as_posix(), []).append(kind)
    return found


def test_only_web_sets_it_and_only_the_operators_delete_calls_it() -> None:
    assert modules_naming(NAME) == {
        "web.py": ["set"],
        "admin/users.py": ["use"],
    }


def test_the_app_installs_the_job_ender_made_by_the_erase_module(menv: Env) -> None:
    ender = menv.app.state.end_account_jobs
    assert callable(ender)
    assert ender.__qualname__ == f"{erase.make_job_ender.__qualname__}.<locals>.end"
    assert ender.__module__ == erase.__name__


def test_the_admin_package_does_not_import_the_jobs_or_erase_code() -> None:
    """The reason for the value: nothing under ``classbeat/admin`` imports them by name."""
    for path in sorted((SRC / "admin").glob("*.py")):
        tree = ast.parse(path.read_text("utf-8"))
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.ImportFrom):
                names = [node.module or "", *(f"{node.module}.{a.name}" for a in node.names)]
            elif isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            for name in names:
                assert not name.startswith(("classbeat.account", "classbeat.jobs")), (
                    path.name,
                    name,
                )
