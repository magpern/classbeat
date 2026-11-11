"""The Dummy billing provider (ADR 0031 section 4): a checkout completes in the same request and
charges nothing. For testers and admins only; M7a has no other provider.

It decides nothing: plans, periods and amounts come from the quote, which comes from
`subscription_policy`. A provider built for a request carries that request's `now`, the same
instant the quote and the writer use, so an upgrade event starts exactly where its quote did.

**Event ids.** `event_id` is a SHA-256 digest of the user id, the action and the request's
`Idempotency-Key`, so a retried click is a duplicate event. The key itself is nowhere in the id
(#953 review: the export says idempotency keys are not exported); only a digest of it is.
"""

from __future__ import annotations

import hashlib
from datetime import datetime
from typing import Final
from uuid import UUID

from classbeat.billing.provider import (
    BillingEvent,
    ChangeScheduled,
    Checkout,
    Started,
    Upgraded,
)
from classbeat.billing.subscription_policy import Quote

PROVIDER: Final = "dummy"


def event_id(owner_id: UUID, action: str, idempotency_key: str) -> str:
    """`dummy_` and 32 hex digits: a digest, so nothing of the key is readable from it."""
    digest = hashlib.sha256(f"{owner_id}\0{action}\0{idempotency_key}".encode()).hexdigest()
    return f"{PROVIDER}_{digest[:32]}"


class DummyProvider:
    """A `BillingProvider` for one request: `now` is the transaction's instant."""

    def __init__(self, now: datetime) -> None:
        self._now = now

    async def checkout(self, quote: Quote, *, owner_id: UUID, idempotency_key: str) -> Checkout:
        ident = event_id(owner_id, "checkout", idempotency_key)
        if quote.kind == "buy":
            return Checkout(
                Started(
                    provider=PROVIDER, event_id=ident, period_start=quote.period_start,
                    period_end=quote.period_end, plan_id=quote.plan_id,
                    amount_minor=quote.amount_minor, currency=quote.currency,
                    anchor_day=quote.anchor_day,
                )
            )  # fmt: skip
        if quote.kind == "upgrade":
            return Checkout(
                Upgraded(
                    provider=PROVIDER, event_id=ident, period_start=self._now,
                    period_end=quote.period_end, plan_id=quote.plan_id,
                    amount_minor=quote.amount_minor, currency=quote.currency,
                )
            )  # fmt: skip
        raise ValueError(f"a checkout charges a buy or an upgrade, not a {quote.kind}")

    async def schedule_change(
        self, quote: Quote, *, owner_id: UUID, idempotency_key: str
    ) -> BillingEvent:
        if quote.kind not in ("downgrade", "cancel", "resume"):
            raise ValueError(f"a change is a downgrade, cancel or resume, not a {quote.kind}")
        return ChangeScheduled(
            provider=PROVIDER,
            event_id=event_id(owner_id, "change", idempotency_key),
            period_start=quote.period_start,
            period_end=quote.period_end,
            next_plan_id=quote.next_plan_id,
            cancel_at_end=quote.cancel_at_end,
        )
