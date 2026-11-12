"""`PaddleProvider`: the `BillingProvider` over a `PaddleClient` (ADR 0032 section 2).

Pure: no I/O of its own; the client is the only seam. Plans, periods and amounts come from the
quote (`subscription_policy`), never from Paddle. Paddle pushes state, so:

- `checkout` of a `buy` creates a hosted checkout and returns `Checkout(None, url, ref)`: the
  route stores the link before the buyer pays, and the signed `transaction.completed` starts the
  subscription later (`billing.webhook`). An `upgrade` asks Paddle to change the price at once;
  its proration charge arrives as `transaction.completed` too, so it also returns no event.
- `schedule_change` (a downgrade, cancel or resume, at the period end) tells Paddle and returns
  the `ChangeScheduled` classbeat records itself at once, with `occurred_at` set to the request's
  `now`: the route stores it as the link's last change time, so an older `subscription.updated`
  delivered later is `stale_change`.

Event ids of classbeat's own changes are `classbeat_` and a digest of the owner, the action and
the request's `Idempotency-Key` (as Dummy's): never a Paddle `evt_` id, and nothing of the key.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from datetime import datetime
from uuid import UUID

from classbeat.billing.paddle_client import CheckoutRequest, PaddleClient, PlanChange, Price
from classbeat.billing.paddle_types import PaddleProvider as ProviderName
from classbeat.billing.provider import BillingEvent, ChangeScheduled, Checkout
from classbeat.billing.subscription_policy import Quote


def digest(owner_id: UUID, action: str, idempotency_key: str) -> str:
    """32 hex digits of SHA-256: nothing of the key is readable from it."""
    return hashlib.sha256(f"{owner_id}\0{action}\0{idempotency_key}".encode()).hexdigest()[:32]


def change_event_id(owner_id: UUID, idempotency_key: str) -> str:
    return f"classbeat_{digest(owner_id, 'change', idempotency_key)}"


class PaddleProvider:
    """A `BillingProvider` for one request: `now` is the transaction's instant, `prices` the
    policy's prices by plan id with their Paddle ids, `subscription_ref` the owner's current
    Paddle subscription (from its link), if any."""

    def __init__(
        self,
        client: PaddleClient,
        *,
        provider: ProviderName,
        now: datetime,
        prices: Mapping[str, Price],
        subscription_ref: str | None,
    ) -> None:
        self._client, self._provider, self._now = client, provider, now
        self._prices, self._subscription = prices, subscription_ref

    def _price(self, plan_id: str) -> Price:
        price = self._prices.get(plan_id)
        if price is None:
            raise ValueError("no Paddle price for the plan")
        return price

    def _sub(self) -> str:
        if self._subscription is None:
            raise ValueError("no Paddle subscription to change")
        return self._subscription

    async def checkout(self, quote: Quote, *, owner_id: UUID, idempotency_key: str) -> Checkout:
        key = digest(owner_id, "checkout", idempotency_key)
        if quote.kind == "buy":
            price = self._price(quote.plan_id)
            if (price.amount_minor, price.currency) != (quote.amount_minor, quote.currency):
                raise ValueError("the Paddle price disagrees with the quote")
            hosted = await self._client.create_checkout(CheckoutRequest(price, owner_id, key))
            return Checkout(None, url=hosted.url, checkout_ref=hosted.checkout_ref)
        if quote.kind == "upgrade":
            change = PlanChange("upgrade", self._price(quote.plan_id), key)
            await self._client.schedule_change(self._sub(), change)
            return Checkout(None)
        raise ValueError(f"a checkout charges a buy or an upgrade, not a {quote.kind}")

    async def schedule_change(
        self, quote: Quote, *, owner_id: UUID, idempotency_key: str
    ) -> BillingEvent:
        key = digest(owner_id, "change", idempotency_key)
        if quote.kind == "cancel":
            await self._client.cancel(self._sub(), idempotency_key=key)
        elif quote.kind == "downgrade" and quote.next_plan_id is not None:
            change = PlanChange("downgrade", self._price(quote.next_plan_id), key)
            await self._client.schedule_change(self._sub(), change)
        elif quote.kind == "resume":
            await self._client.schedule_change(self._sub(), PlanChange("resume", None, key))
        else:
            raise ValueError(f"a change is a downgrade, cancel or resume, not a {quote.kind}")
        return ChangeScheduled(
            provider=self._provider, event_id=change_event_id(owner_id, idempotency_key),
            period_start=quote.period_start, period_end=quote.period_end,
            next_plan_id=quote.next_plan_id, cancel_at_end=quote.cancel_at_end,
            occurred_at=self._now,
        )  # fmt: skip
