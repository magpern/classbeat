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


def test_webhook_sessions_are_opened_only_by_the_webhook() -> None:
    """ADR 0032 section 3 (#973): the maint role without a user session, for a signature-verified
    notification only; the `billing_session` guard above stays as it was."""
    for function in ("webhook_session", "webhook_lookup", "record_webhook_refusal"):
        assert callers(function) - {"classbeat.admin.db"} == {"classbeat.billing.webhook"}, function


def test_the_webhook_writes_its_log_rows_with_the_webhook_action_only() -> None:
    tree = modules()["classbeat.admin.db"]
    webhook = next(
        n for n in ast.walk(tree)
        if isinstance(n, ast.AsyncFunctionDef) and n.name == "webhook_session"
    )  # fmt: skip
    literals = {c.value for c in ast.walk(webhook) if isinstance(c, ast.Constant)}
    assert "webhook" in literals
    assert not literals & {"checkout", "change", "prices"}


def test_the_writer_is_imported_only_by_its_three_callers() -> None:
    allowed = {"classbeat.billing.routes", "classbeat.admin.billing", "classbeat.billing.webhook"}
    assert importers("classbeat.billing.writer") == allowed
    sql_users = {"classbeat.billing.writer", "classbeat.billing.views"} | allowed
    assert importers("classbeat.billing.writer_sql") <= sql_users


def test_the_link_statements_are_imported_only_by_the_paddle_paths() -> None:
    assert importers("classbeat.billing.links_sql") == {
        "classbeat.billing.hosted", "classbeat.billing.webhook"
    }  # fmt: skip


def test_the_fake_paddle_client_is_built_only_at_setup() -> None:
    """The fake is mounted only by `billing.setup` (which the settings gate keeps out of prod)."""
    assert callers("FakePaddleClient") - {"classbeat.billing.fake_paddle"} == {
        "classbeat.billing.setup"
    }


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
        "paddle_signature", "paddle_payload", "paddle_types", "paddle_paid", "paddle_events",
        "paddle_subscription", "paddle_client", "paddle_provider", "paddle_catalog",
        "paddle_links",
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


PADDLE_IMPORTS = {
    "__future__", "collections.abc", "dataclasses", "datetime", "hashlib", "hmac", "json", "re",
    "typing", "uuid", "classbeat.billing", "classbeat.billing.apply_policy",
    "classbeat.billing.paddle_paid", "classbeat.billing.paddle_payload",
    "classbeat.billing.paddle_subscription", "classbeat.billing.paddle_types",
    "classbeat.billing.periods", "classbeat.billing.provider",
    "classbeat.billing.subscription_policy", "classbeat.billing.paddle_client",
}  # fmt: skip
PADDLE_MODULES = {
    f"classbeat.billing.paddle_{name}"
    for name in (
        "signature", "payload", "types", "paid", "events", "subscription", "client", "provider",
        "catalog", "links",
    )
}  # fmt: skip


def test_the_paddle_layer_imports_only_its_allowlist() -> None:
    """ADR 0032 section 1: signature and mapping are pure; a new import is reviewed here first."""
    found = [name for name in modules() if name.startswith("classbeat.billing.paddle_")]
    assert set(found) == PADDLE_MODULES
    for name in found:
        for node in ast.walk(modules()[name]):
            if isinstance(node, ast.ImportFrom):
                assert node.module in PADDLE_IMPORTS, (name, node.module)
            elif isinstance(node, ast.Import):
                assert {a.name for a in node.names} <= PADDLE_IMPORTS, name
            elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                assert node.func.id not in {"open", "print", "exec", "eval", "__import__"}, name
