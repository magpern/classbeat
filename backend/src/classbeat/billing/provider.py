"""The billing provider interface and its provider-neutral events (ADR 0031 section 4).

Pure types: no I/O here. A provider never decides plans, periods or amounts; they come from
`subscription_policy`. There is no refund event or method: nothing is ever paid back.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Protocol
from uuid import UUID

from classbeat.billing.subscription_policy import Quote

EventKind = Literal["started", "renewed", "upgraded", "change_scheduled", "ended"]


@dataclass(frozen=True, kw_only=True)
class _Event:
    provider: str
    event_id: str
    period_start: datetime
    period_end: datetime


@dataclass(frozen=True, kw_only=True)
class _Paid(_Event):
    plan_id: str
    amount_minor: int
    currency: str


@dataclass(frozen=True, kw_only=True)
class Started(_Paid):
    kind: Literal["started"] = "started"
    anchor_day: int


@dataclass(frozen=True, kw_only=True)
class Renewed(_Paid):
    kind: Literal["renewed"] = "renewed"


@dataclass(frozen=True, kw_only=True)
class Upgraded(_Paid):
    kind: Literal["upgraded"] = "upgraded"


@dataclass(frozen=True, kw_only=True)
class ChangeScheduled(_Event):
    """A downgrade (`next_plan_id`), a cancel (`cancel_at_end`) or a resume (both clear)."""

    kind: Literal["change_scheduled"] = "change_scheduled"
    next_plan_id: str | None = None
    cancel_at_end: bool = False


@dataclass(frozen=True, kw_only=True)
class Ended(_Event):
    kind: Literal["ended"] = "ended"


BillingEvent = Started | Renewed | Upgraded | ChangeScheduled | Ended


@dataclass(frozen=True)
class Checkout:
    """The outcome of a checkout: for Dummy, the completed event; a real provider may defer."""

    event: BillingEvent | None


class BillingProvider(Protocol):
    async def checkout(self, quote: Quote, *, owner_id: UUID, idempotency_key: str) -> Checkout:
        """Charge a `buy` or `upgrade` quote; a retried key yields the same event id."""
        ...

    async def schedule_change(
        self, quote: Quote, *, owner_id: UUID, idempotency_key: str
    ) -> BillingEvent:
        """Record a `downgrade`, `cancel` or `resume` quote, effective at the period end."""
        ...
