"""Who may raise or pass ``AdminSkipped`` (#994, #999): a static guard over the source, no
database. ``admin_session`` writes no ``refused`` row for an ``AdminSkipped``, so one raised
anywhere else would silence a real refusal row. Today the bulk decline is the only user: it passes
the class to ``_decline``, whose ``_pending`` raises it, catches it itself and writes one summary
row for the call. An allowlist, so a new use fails here until a reviewer has looked at it.

Limit: a static check sees only names that are spelled out. A lookup that never spells the name
(string concatenation, ``vars``, ``__subclasses__``) is outside any static check; a reviewer has to
catch it."""

import ast

from test_billing_guard import modules

NAME = "AdminSkipped"
INVITE_REQUESTS = "classbeat.admin.invite_requests"


def _function(tree: ast.Module, name: str) -> ast.AsyncFunctionDef | ast.FunctionDef:
    return next(
        n for n in ast.walk(tree)
        if isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef) and n.name == name
    )  # fmt: skip


def _uses(tree: ast.AST) -> list[tuple[str, ast.AST]]:
    """Each reference to the name (``AdminSkipped`` or ``x.AdminSkipped``) with the innermost
    function around it (``<module>`` outside any)."""
    found: list[tuple[str, ast.AST]] = []

    def visit(node: ast.AST, where: str) -> None:
        for child in ast.iter_child_nodes(node):
            inner = where
            if isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef):
                inner = child.name
            if (isinstance(child, ast.Name) and child.id == NAME) or (
                isinstance(child, ast.Attribute) and child.attr == NAME
            ):
                found.append((where, child))
            visit(child, inner)

    visit(tree, "<module>")
    return found


def test_the_name_is_used_only_by_the_session_and_the_bulk_decline() -> None:
    uses = {
        (module, where) for module, tree in modules().items() for where, _ in _uses(tree)
    }  # fmt: skip
    assert uses == {
        ("classbeat.admin.db", "_record_failure"),  # the isinstance that skips the row
        (INVITE_REQUESTS, "decline_requests"),
    }


def test_the_name_is_imported_plainly_and_never_spelled_as_a_string() -> None:
    importers: set[str] = set()
    for module, tree in modules().items():
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                for alias in node.names:
                    if alias.name == NAME:
                        assert alias.asname is None, module  # no alias hides a use
                        assert node.module == "classbeat.admin.principals", module
                        importers.add(module)
            if isinstance(node, ast.Constant):
                assert node.value != NAME, module  # no getattr(..., "AdminSkipped")
    assert importers == {"classbeat.admin.db", INVITE_REQUESTS}


def test_the_bulk_decline_passes_it_to_decline_and_catches_it_itself() -> None:
    tree = modules()[INVITE_REQUESTS]
    bulk = _function(tree, "decline_requests")
    nodes = [node for _, node in _uses(bulk)]
    calls = [
        c for c in ast.walk(bulk)
        if isinstance(c, ast.Call) and any(a in nodes for a in c.args)
    ]  # fmt: skip
    assert [ast.unparse(c.func) for c in calls] == ["_decline"]
    handlers = [h for h in ast.walk(bulk) if isinstance(h, ast.ExceptHandler) and h.type in nodes]
    assert len(handlers) == 1
    assert len(nodes) == 2  # the argument and the except, nothing else
    guards = [
        t for t in ast.walk(bulk)
        if isinstance(t, ast.Try) and handlers[0] in t.handlers
    ]  # fmt: skip
    assert len(guards) == 1, f"expected one try with the AdminSkipped handler, found {len(guards)}"
    assert calls[0] in {n for s in guards[0].body for n in ast.walk(s)}
    # The summary row is written in a finally that wraps the decline loop itself (the `for` or
    # `async for` around the `_decline` call), so a later id's error keeps the earlier skips' row.
    # A `finally` around one `_decline` call, or an unrelated one, does not count; nor does a `try`
    # around the loop that has only `except` handlers.
    loops = [
        n for n in ast.walk(bulk)
        if isinstance(n, ast.For | ast.AsyncFor) and calls[0] in set(ast.walk(n))
    ]  # fmt: skip
    assert len(loops) == 1, f"expected one loop around the _decline call, found {len(loops)}"
    wrapping = [
        t for t in ast.walk(bulk)
        if isinstance(t, ast.Try)
        and t.finalbody
        and loops[0] in {n for s in t.body for n in ast.walk(s)}
    ]  # fmt: skip
    assert len(wrapping) == 1, (
        f"expected one try/finally around the decline loop, found {len(wrapping)}"
    )
    finals = [n for s in wrapping[0].finalbody for n in ast.walk(s)]
    summary = [
        n for n in finals if isinstance(n, ast.Call) and ast.unparse(n.func) == "audit_refusal"
    ]
    assert len(summary) == 1
    assert "invite_request.decline_skipped" in {
        a.value for a in summary[0].args if isinstance(a, ast.Constant)
    }


def test_the_class_reaches_only_a_raise_in_pending() -> None:
    """``_decline`` hands its ``refused`` class only to ``_pending``, which only raises it; no
    other function takes a ``refused`` class to raise."""
    tree = modules()[INVITE_REQUESTS]
    takers = {
        n.name for n in ast.walk(tree)
        if isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef)
        and any(a.arg == "refused" for a in n.args.args + n.args.kwonlyargs)
    }  # fmt: skip
    assert takers == {"_decline", "_pending"}
    decline, pending = _function(tree, "_decline"), _function(tree, "_pending")
    passed = [
        c for c in ast.walk(decline)
        if isinstance(c, ast.Call) and any(isinstance(a, ast.Name) and a.id == "refused"
                                           for a in [*c.args, *(k.value for k in c.keywords)])
    ]  # fmt: skip
    assert [ast.unparse(c.func) for c in passed] == ["_pending"]
    refs = [n for n in ast.walk(decline) if isinstance(n, ast.Name) and n.id == "refused"]
    assert len(refs) == 1  # that one argument
    raised = [
        r.exc.func for r in ast.walk(pending)
        if isinstance(r, ast.Raise) and isinstance(r.exc, ast.Call)
    ]  # fmt: skip
    refs = [n for n in ast.walk(pending) if isinstance(n, ast.Name) and n.id == "refused"]
    assert refs
    assert all(r in raised for r in refs)  # each use is `raise refused(...)`
