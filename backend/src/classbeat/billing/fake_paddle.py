"""`FakePaddleClient`: Paddle's lifecycle simulated in memory (ADR 0032 section 2). Test and
compose only: the settings refuse `fake_paddle` in prod (`config_billing`), and nothing here
opens a connection.

It is a `PaddleClient` (a hosted checkout URL on our own origin, plan changes, cancels) and it
plays Paddle's side: `pay` and `fail` complete or fail a checkout, `renew` ends a period, and
every notification it would send is queued in `outbox` as a `Delivery`, signed with the test
secret when it is sent (`sign`), like Paddle's `Paddle-Signature`. The caller decides how
deliveries reach the webhook: `deliver` sends one round (in any `order`, `hold`-ing some back to
delay them) and keeps what was not answered 2xx for the next round (Paddle's retry); `send`
sends one again (a duplicate or a replay).

The payloads follow Paddle's public examples (`tests/fixtures/paddle/`); amounts follow ADR 0031
section 5, the policy's own numbers (an upgrade's proration by `subscription_policy`).
"""

from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Awaitable, Callable, Collection, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Final
from uuid import UUID

from classbeat.billing.paddle_client import CheckoutRequest, HostedCheckout, PlanChange, Price
from classbeat.billing.paddle_signature import sign
from classbeat.billing.paddle_types import OWNER_KEY
from classbeat.billing.periods import days_in_period, days_left, period_end
from classbeat.billing.subscription_policy import upgrade_amount

CHECKOUT_PATH: Final = "/fake-paddle/checkout"  # the hosted page (PB-3 serves it)

Post = Callable[[bytes, str], Awaitable[int]]  # (body, Paddle-Signature) -> HTTP status


@dataclass(frozen=True)
class Delivery:
    event_id: str
    event_type: str
    body: bytes


@dataclass
class _Sub:
    ref: str
    customer_ref: str
    owner_id: UUID
    price: Price
    start: datetime
    end: datetime
    anchor_day: int
    next_price: Price | None = None
    cancel: bool = False
    status: str = "active"


def _iso(moment: datetime) -> str:
    return moment.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _period(start: datetime, end: datetime) -> dict[str, str]:
    return {"starts_at": _iso(start), "ends_at": _iso(end)}


def _price(price: Price) -> dict[str, Any]:
    return {
        "id": price.ref, "product_id": "pro_fakeclassbeat", "description": "Monthly",
        "billing_cycle": {"interval": "month", "frequency": 1}, "trial_period": None,
        "unit_price": {"amount": str(price.amount_minor), "currency_code": price.currency},
        "status": "active",
    }  # fmt: skip


class FakePaddleClient:
    def __init__(
        self,
        *,
        secret: str,
        base_url: str,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._secret, self._base = secret, base_url.rstrip("/")
        self._clock = clock or (lambda: datetime.now(UTC))
        self._seq = 0
        self._by_key: dict[str, HostedCheckout] = {}
        self._checkouts: dict[str, CheckoutRequest] = {}
        self._customers: dict[UUID, str] = {}
        self.subscriptions: dict[str, _Sub] = {}
        self.outbox: list[Delivery] = []

    # --- ids, time and notifications ---------------------------------------------------------

    def _id(self, prefix: str) -> str:
        self._seq += 1
        return f"{prefix}_{hashlib.sha256(f'{id(self)}:{self._seq}'.encode()).hexdigest()[:26]}"

    def _now(self) -> datetime:
        return self._clock().astimezone(UTC).replace(microsecond=0)

    def _queue(self, event_type: str, data: dict[str, Any]) -> Delivery:
        event_id = self._id("evt")
        doc = {"event_id": event_id, "event_type": event_type, "occurred_at": _iso(self._now()),
               "notification_id": self._id("ntf"), "data": data}  # fmt: skip
        delivery = Delivery(event_id, event_type, json.dumps(doc).encode())
        self.outbox.append(delivery)
        return delivery

    def _transaction(
        self, sub: _Sub, ref: str, origin: str, price: Price, amount: int,
        span: tuple[datetime, datetime],
    ) -> Delivery:  # fmt: skip
        item: dict[str, Any] = {"price_id": price.ref, "price": _price(price), "quantity": 1}
        if origin == "subscription_update":
            item["proration"] = {"rate": "1", "billing_period": _period(*span)}
            billing = _period(sub.start, sub.end)
        else:
            billing = _period(*span)
        return self._queue("transaction.completed", {
            "id": ref, "status": "completed", "customer_id": sub.customer_ref, "origin": origin,
            "custom_data": {OWNER_KEY: str(sub.owner_id)}, "subscription_id": sub.ref,
            "currency_code": price.currency, "billing_period": billing, "items": [item],
            "details": {"totals": {"subtotal": str(amount), "discount": "0", "tax": "0",
                                   "total": str(amount), "currency_code": price.currency}},
        })  # fmt: skip

    def _subscription(self, sub: _Sub, event_type: str = "subscription.updated") -> Delivery:
        change = (
            {"action": "cancel", "effective_at": _iso(sub.end), "resume_at": None}
            if sub.cancel
            else None
        )
        price = sub.next_price or sub.price  # Paddle shows the new items from the change on
        return self._queue(event_type, {
            "id": sub.ref, "status": sub.status, "customer_id": sub.customer_ref,
            "currency_code": sub.price.currency,
            "current_billing_period": _period(sub.start, sub.end),
            "billing_cycle": {"interval": "month", "frequency": 1}, "scheduled_change": change,
            "items": [{"status": "active", "quantity": 1, "price": _price(price)}],
            "custom_data": {OWNER_KEY: str(sub.owner_id)},
        })  # fmt: skip

    # --- PaddleClient --------------------------------------------------------------------------

    async def create_checkout(self, request: CheckoutRequest) -> HostedCheckout:
        found = self._by_key.get(request.idempotency_key)
        if found is not None:
            return found
        ref = self._id("txn")
        hosted = HostedCheckout(ref, f"{self._base}{CHECKOUT_PATH}/{ref}")
        self._by_key[request.idempotency_key] = hosted
        self._checkouts[ref] = request
        return hosted

    async def schedule_change(self, subscription_ref: str, change: PlanChange) -> None:
        sub = self.subscriptions[subscription_ref]
        if change.kind == "resume":
            sub.next_price, sub.cancel = None, False
        elif change.kind == "downgrade" and change.price is not None:
            sub.next_price, sub.cancel = change.price, False
        elif change.kind == "upgrade" and change.price is not None:
            now = self._now()
            diff = change.price.amount_minor - sub.price.amount_minor
            left = days_left(sub.end, now)
            amount = upgrade_amount(diff, left, days_in_period(sub.start, sub.end))
            sub.price, sub.next_price, sub.cancel = change.price, None, False
            self._transaction(sub, self._id("txn"), "subscription_update", change.price, amount,
                              (now, sub.end))  # fmt: skip
        else:
            raise ValueError("not a plan change")
        self._subscription(sub)

    async def cancel(self, subscription_ref: str, *, idempotency_key: str) -> None:
        sub = self.subscriptions[subscription_ref]
        sub.cancel, sub.next_price = True, None
        self._subscription(sub)

    # --- Paddle's side of the lifecycle --------------------------------------------------------

    def pay(self, checkout_ref: str) -> str:
        """The buyer pays on the hosted page: a subscription starts now. Its id."""
        request = self._checkouts.pop(checkout_ref)
        customer = self._customers.setdefault(request.owner_id, self._id("ctm"))
        start = self._now()
        sub = _Sub(self._id("sub"), customer, request.owner_id, request.price, start,
                   period_end(start, start.day), start.day)  # fmt: skip
        self.subscriptions[sub.ref] = sub
        self._subscription(sub, "subscription.created")
        self._transaction(sub, checkout_ref, "web", sub.price, sub.price.amount_minor,
                          (sub.start, sub.end))  # fmt: skip
        return sub.ref

    def fail(self, checkout_ref: str) -> Delivery:
        """The card is declined: Paddle notifies, nothing starts."""
        request = self._checkouts[checkout_ref]
        return self._queue("transaction.payment_failed", {
            "id": checkout_ref, "status": "past_due", "origin": "web",
            "custom_data": {OWNER_KEY: str(request.owner_id)},
        })  # fmt: skip

    def renew(self, subscription_ref: str) -> Delivery:
        """The period ends: a renewal at the scheduled or current price, or the cancel."""
        sub = self.subscriptions[subscription_ref]
        if sub.cancel:
            sub.status = "canceled"
            return self._queue("subscription.canceled", {
                "id": sub.ref, "status": "canceled", "customer_id": sub.customer_ref,
                "canceled_at": _iso(sub.end), "current_billing_period": None,
                "custom_data": {OWNER_KEY: str(sub.owner_id)},
            })  # fmt: skip
        sub.price, sub.next_price = sub.next_price or sub.price, None
        sub.start, sub.end = sub.end, period_end(sub.end, sub.anchor_day)
        return self._transaction(sub, self._id("txn"), "subscription_recurring", sub.price,
                                 sub.price.amount_minor, (sub.start, sub.end))  # fmt: skip

    # --- delivering ------------------------------------------------------------------------------

    def sign(self, delivery: Delivery, *, ts: int | None = None) -> str:
        """The `Paddle-Signature` header for `delivery`, signed now (or at `ts`)."""
        return sign(delivery.body, self._secret, ts=int(time.time()) if ts is None else ts)

    async def send(self, delivery: Delivery, post: Post, *, ts: int | None = None) -> int:
        """Send one delivery (again: a duplicate, a replay); the outbox is not touched."""
        return await post(delivery.body, self.sign(delivery, ts=ts))

    async def deliver(
        self, post: Post, *, order: Sequence[int] | None = None, hold: Collection[str] = ()
    ) -> list[int]:
        """One round: every queued delivery (by index in `order`, else oldest first) whose
        `event_id` is not held is sent; one answered 2xx leaves the outbox, any other stays for
        the next round, as Paddle retries. The statuses, in the order sent."""
        pending = list(self.outbox)
        picked = [pending[i] for i in order] if order is not None else pending
        statuses: list[int] = []
        for delivery in picked:
            if delivery.event_id in hold:
                continue
            status = await self.send(delivery, post)
            statuses.append(status)
            if 200 <= status < 300:
                self.outbox.remove(delivery)
        return statuses
