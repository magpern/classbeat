"""The provider interface: neutral events, no refund, and the pure-module import rule."""

import ast
from datetime import UTC, datetime
from pathlib import Path
from types import ModuleType
from typing import Any
from uuid import UUID

from classbeat.billing import periods, provider, subscription_policy
from classbeat.billing.provider import (
    BillingEvent,
    BillingProvider,
    ChangeScheduled,
    Checkout,
    Ended,
    Renewed,
    Started,
    Upgraded,
)
from classbeat.billing.subscription_policy import Quote

ALLOWED_IMPORTS = {
    "__future__",
    "calendar",
    "classbeat.billing.periods",
    "dataclasses",
    "datetime",
    "typing",
    "uuid",
    "classbeat.billing.subscription_policy",
}
T0 = datetime(2025, 1, 1, tzinfo=UTC)
T1 = datetime(2025, 2, 1, tzinfo=UTC)


def tree_of(module: ModuleType) -> ast.Module:
    assert module.__file__ is not None
    return ast.parse(Path(module.__file__).read_text(encoding="utf-8"))


def imported(module: ModuleType) -> set[str]:
    tree = tree_of(module)
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            names.add(node.module or "")
    return names


def test_pure_modules_import_only_the_allowlist() -> None:
    for module in (subscription_policy, periods, provider):
        assert imported(module) <= ALLOWED_IMPORTS, module.__name__


def test_pure_modules_call_no_io_builtins() -> None:
    for module in (subscription_policy, periods, provider):
        calls = {
            n.func.id
            for n in ast.walk(tree_of(module))
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
        }
        assert not calls & {"open", "print", "exec", "eval", "__import__"}


def test_event_kinds_are_the_five_and_none_is_a_refund() -> None:
    def base(n: str) -> dict[str, Any]:
        return {"provider": "dummy", "event_id": n, "period_start": T0, "period_end": T1}

    paid: dict[str, Any] = {"plan_id": "pro", "amount_minor": 0, "currency": "USD"}
    events: list[BillingEvent] = [
        Started(**base("a"), **paid, anchor_day=1),
        Renewed(**base("b"), **paid),
        Upgraded(**base("c"), **paid),
        ChangeScheduled(**base("d"), next_plan_id="free"),
        Ended(**base("e")),
    ]
    kinds = ["started", "renewed", "upgraded", "change_scheduled", "ended"]
    assert [e.kind for e in events] == kinds
    assert not [name for name in dir(provider) if "refund" in name.lower()]


def test_change_scheduled_defaults() -> None:
    e = ChangeScheduled(provider="dummy", event_id="e", period_start=T0, period_end=T1)
    assert (e.next_plan_id, e.cancel_at_end) == (None, False)


class _Fake:
    async def checkout(self, quote: Quote, *, owner_id: UUID, idempotency_key: str) -> Checkout:
        return Checkout(None)

    async def schedule_change(
        self, quote: Quote, *, owner_id: UUID, idempotency_key: str
    ) -> BillingEvent:
        return ChangeScheduled(
            provider="x", event_id=idempotency_key, period_start=T0, period_end=T1
        )


def test_protocol_is_implementable_and_has_no_refund_method() -> None:
    fake: BillingProvider = _Fake()
    assert fake is not None
    assert not [n for n in vars(BillingProvider) if "refund" in n]
