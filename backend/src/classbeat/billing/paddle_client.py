"""The `PaddleClient` protocol: the only network seam of the Paddle provider (ADR 0032 section 2).

Pure: types only. `PaddleProvider` (`paddle_provider`) calls it; `FakePaddleClient`
(`billing.fake_paddle`, test and compose only) implements it. The real HTTP client is not
written: it is the owner's gated step (M7b-real). A client never decides a plan, period or
amount: what it is asked for comes from `subscription_policy`, and the money it charges comes
back only as a signed notification the webhook checks against the policy again.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol
from uuid import UUID


@dataclass(frozen=True)
class Price:
    """A plan's Paddle price: its id in Paddle and the policy's monthly amount for it."""

    ref: str
    plan_id: str
    amount_minor: int
    currency: str


@dataclass(frozen=True)
class CheckoutRequest:
    """A `buy`: one monthly price for `owner_id`. `idempotency_key` is a digest, so a retried
    click gets the same checkout."""

    price: Price
    owner_id: UUID
    idempotency_key: str


@dataclass(frozen=True)
class HostedCheckout:
    """Where the buyer pays (Paddle's hosted page) and the transaction it will complete."""

    checkout_ref: str
    url: str


ChangeKind = Literal["upgrade", "downgrade", "resume"]


@dataclass(frozen=True)
class PlanChange:
    """`upgrade`: the new price at once, prorated by Paddle (it charges and notifies);
    `downgrade`: the new price from the next period; `resume`: drop a scheduled change."""

    kind: ChangeKind
    price: Price | None
    idempotency_key: str


class PaddleClient(Protocol):
    async def create_checkout(self, request: CheckoutRequest) -> HostedCheckout:
        """A transaction for the buyer to pay on the hosted page; nothing is charged yet."""
        ...

    async def schedule_change(self, subscription_ref: str, change: PlanChange) -> None:
        """Change the subscription's price now (upgrade) or at the period end, or resume it."""
        ...

    async def cancel(self, subscription_ref: str, *, idempotency_key: str) -> None:
        """Cancel at the end of the current period (never at once: nothing is paid back)."""
        ...
