"""Who may reach the billing write path (#948, ADR 0031 section 3): static guards over the source.
No database. Allowlists, so a new caller fails here until a reviewer has looked at it."""

import ast
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src" / "classbeat"


def modules() -> dict[str, ast.Module]:
    return {
        ".".join(p.relative_to(SRC.parent).with_suffix("").parts): ast.parse(p.read_text("utf-8"))
        for p in SRC.rglob("*.py")
    }


def importers(target: str) -> set[str]:
    """Modules that import `target` (the module or a name from it, or a name of its package)."""
    package, _, leaf = target.rpartition(".")
    found: set[str] = set()
    for name, tree in modules().items():
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module is not None:
                if node.module == target or (
                    node.module == package and any(a.name == leaf for a in node.names)
                ):
                    found.add(name)
            elif isinstance(node, ast.Import) and any(a.name == target for a in node.names):
                found.add(name)
    return found


def callers(function: str) -> set[str]:
    found: set[str] = set()
    for name, tree in modules().items():
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                called = node.func
                label = called.id if isinstance(called, ast.Name) else getattr(called, "attr", "")
                if label == function:
                    found.add(name)
    return found


def test_billing_session_is_opened_only_by_the_billing_routes() -> None:
    assert callers("billing_session") - {"classbeat.admin.db"} == {"classbeat.billing.routes"}


def test_buyer_is_built_only_by_the_billing_routes() -> None:
    assert callers("Buyer") - {"classbeat.admin.db"} == {"classbeat.billing.routes"}


def test_actor_is_built_only_by_the_admin_gate() -> None:
    assert callers("Actor") - {"classbeat.admin.db"} == {"classbeat.admin.routes"}


def test_the_writer_is_imported_only_by_its_two_callers() -> None:
    allowed = {"classbeat.billing.routes", "classbeat.admin.billing"}
    assert importers("classbeat.billing.writer") == allowed
    assert importers("classbeat.billing.writer_sql") <= {"classbeat.billing.writer"} | allowed


def test_the_price_reader_opens_no_session() -> None:
    tree = modules()["classbeat.billing.prices"]
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            called = node.func
            label = called.id if isinstance(called, ast.Name) else getattr(called, "attr", "")
            assert label not in {"admin_session", "billing_session", "AsyncSession", "begin"}
        if isinstance(node, ast.ImportFrom):
            assert node.module not in {"classbeat.admin.db", "classbeat.db"}


def test_the_pure_billing_modules_import_no_io() -> None:
    pure = {
        "apply_policy", "choices", "fast_forward", "subscription_policy", "periods", "provider",
    }  # fmt: skip
    for name in pure:
        for node in ast.walk(modules()[f"classbeat.billing.{name}"]):
            names = (
                [node.module or ""]
                if isinstance(node, ast.ImportFrom)
                else [a.name for a in node.names]
                if isinstance(node, ast.Import)
                else []
            )
            for module in names:
                assert not module.startswith(("sqlalchemy", "httpx", "fastapi", "classbeat.db")), (
                    name,
                    module,
                )
