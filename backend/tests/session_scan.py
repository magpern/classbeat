"""The fence guard of ADR 0005 section 3: only allowlisted modules may open a ``user_session``.

Every write a worker makes for a job goes through ``classbeat.jobs.fenced_session``, a
``user_session`` whose first statement is the fence. So ``user_session`` itself may be used only
by the packages that own data and their own transactions:

- four modules of ``classbeat.jobs``, exactly: ``classbeat.jobs.fence`` (``fenced_session``),
  ``classbeat.jobs.credits`` (the settle step), ``classbeat.jobs.service`` (the jobs API: the
  user's own create, cancel and approve in a web request, never imported by the worker) and
  ``classbeat.jobs.progress`` (the SSE stream's read-only, owner-scoped reads of a job's state
  and events, J4). The rest of the package, and every job kind, is scanned like any other code:
  **job kinds live outside ``classbeat.jobs``** (X1, D1 and M4 put theirs in their own packages)
  and write through ``fenced_session``;
- ``classbeat.connections`` (ADR 0004; during a run, its status, ``last_error`` and
  ``last_used_at`` writes are the one exemption from the fence: they are conditional on
  ``wrapped_dek`` and touch no job data);
- ``classbeat.auth`` (the web issuer);
- ``classbeat.db`` (where it is defined and exported);
- ``classbeat.operations.user`` (the user's **Check again** and **Do it anyway** on their own
  external operations, ADR 0005 section 7: user actions in the web process, never worker writes;
  the rest of ``classbeat.operations`` writes through the fence);
- ``classbeat.operations.orphans`` (ADR 0007 section 8: marks the user's orphaned ``pending``
  class writes ``uncertain`` before a view or action in the web process; on the worker path it
  is handed the job's fenced session and opens none);
- ``classbeat.saving.reads`` (ADR 0007 section 8, SV1: the save reconciler's one read-only
  ``SELECT`` of the saved revision's content, in the worker's reconciliation and the user's Check
  again; a reconciler gets a session factory, not a transaction, and writes nothing);
- ``classbeat.classes`` (ADR 0007 section 1, C2: the user's own reads of their classes, the
  current-revision pick and the class delete in a web request, never the worker);
- ``classbeat.keys_cli`` (the operator's ``keys wipe``, which predates this guard: it runs as the
  owner role from the CLI, never in the worker, and deletes connections and TOTP per owner);
- `classbeat.plans.account`, `classbeat.plans.redeem` and `classbeat.plans.trial` (ADR 0011
  sections 2 and 9, PL1: the user's own plan read, code redemption and trial start in a web
  request; only `classbeat.plans.routes` reaches them, never the worker).

A later owned-data package joins the list in its own PR, with its reason. Anything else, the
worker and agent code included, fails ``scan_source``: a reference to ``user_session`` or
``_owned_session`` by any name, attribute, import alias or string, and any non-docstring string
that sets ``app.user_id`` itself. Like the other guards it catches accidental use and makes the
rest visible in review; it cannot stop obfuscated code.
"""

import ast

# Packages (the package and everything under it) and exact modules.
ALLOWED_PREFIXES = (
    "classbeat.connections",
    "classbeat.auth",
    "classbeat.db",
    "classbeat.classes",
    "classbeat.usage",  # the monthly and per-job reads of agent_runs (US1)
)
ALLOWED_MODULES = (
    "classbeat.jobs.fence",
    "classbeat.jobs.credits",
    "classbeat.jobs.service",  # the jobs API: the user's own writes in a web request (J3)
    "classbeat.jobs.progress",
    "classbeat.operations.user",
    "classbeat.operations.orphans",  # ADR 0007 section 8 (X2)
    "classbeat.saving.reads",  # the save reconciler's read of the revision (SV1)
    "classbeat.keys_cli",
    # The user's own plan read, redeem and trial in a web request (ADR 0011 section 2, PL1).
    "classbeat.plans.account",
    "classbeat.plans.redeem",
    "classbeat.plans.trial",
)
NAMES = frozenset({"user_session", "_owned_session"})
SESSION_MODULE = "classbeat.db.session"


def allowed(module: str) -> bool:
    if module in ALLOWED_MODULES:
        return True
    return any(module == p or module.startswith(p + ".") for p in ALLOWED_PREFIXES)


def _docstrings(tree: ast.AST) -> set[int]:
    ids: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
            body = node.body
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
                ids.add(id(body[0].value))
    return ids


def scan_source(source: str, module: str) -> list[tuple[int, str]]:
    """What ``module`` may not do; empty for an allowlisted module."""
    if allowed(module):
        return []
    tree = ast.parse(source)
    docs = _docstrings(tree)
    found: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        line = getattr(node, "lineno", 0)
        if isinstance(node, ast.Name) and node.id in NAMES:
            found.append((line, f"uses {node.id}"))
        elif isinstance(node, ast.Attribute) and node.attr in NAMES:
            found.append((line, f"uses {node.attr}"))
        elif isinstance(node, ast.alias) and (
            node.name.rsplit(".", 1)[-1] in NAMES or node.name == SESSION_MODULE
        ):
            found.append((line, f"imports {node.name}"))
        elif isinstance(node, ast.ImportFrom):
            if node.module == SESSION_MODULE:
                found.append((line, "imports from classbeat.db.session"))
            elif node.module == "classbeat.db" and any(a.name == "session" for a in node.names):
                found.append((line, "imports classbeat.db.session"))
        elif (
            isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docs
        ):
            if node.value in NAMES or node.value == SESSION_MODULE:
                found.append((line, f"names {node.value!r}"))
            elif "app.user_id" in node.value:
                found.append((line, "sets app.user_id itself"))
    return found
