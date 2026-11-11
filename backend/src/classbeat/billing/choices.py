"""Which quote a plan choice gets (ADR 0031 sections 3 and 5). Pure: no I/O, no clock.

Wraps `subscription_policy` for the two billing routes: a checkout is a `buy` (no active
subscription) or an `upgrade`; a change is a `downgrade`, `cancel` or `resume`, and choosing
Free is always a cancel (never routed through the downgrade quote).
"""

from __future__ import annotations

from datetime import datetime

from classbeat.billing.apply_policy import State
from classbeat.billing.periods import as_utc
from classbeat.billing.subscription_policy import (
    Current,
    Offer,
    Quote,
    Refusal,
    quote_buy,
    quote_change,
    quote_upgrade,
)

FREE_PLAN = "free"


def current_of(state: State, rank: int) -> Current:
    return Current(
        plan_id=state.plan_id, rank=rank, period_start=state.period_start,
        period_end=state.period_end, anchor_day=state.anchor_day, basis_minor=state.basis_minor,
        basis_currency=state.basis_currency, next_plan_id=state.next_plan_id,
        cancel_at_end=state.cancel_at_end,
    )  # fmt: skip


def in_period(state: State, now: datetime) -> bool:
    """`quote_upgrade` charges one day after the period end and overcharges before its start, so
    the writer refuses an upgrade outside the stored period (#954 review)."""
    return as_utc(state.period_start) <= as_utc(now) < as_utc(state.period_end)


def checkout_quote(
    state: State | None, rank: int | None, offer: Offer, now: datetime
) -> Quote | Refusal | str:
    """A `buy` without an active subscription, else an `upgrade`. A string is a refusal reason
    of the writer's own: `already_on_plan` (the same plan, nothing to buy) or `outside_period`."""
    if state is None or state.status != "active":
        return quote_buy(offer, now)
    if offer.plan_id == state.plan_id:
        return "already_on_plan"
    if not in_period(state, now):
        return "outside_period"
    if rank is None:  # the caller reads the stored plan's rank with the subscription
        raise ValueError("an active subscription needs its plan's rank")
    return quote_upgrade(current_of(state, rank), offer, now)


def change_quote(
    state: State | None, rank: int | None, target: Offer | None, now: datetime
) -> Quote | Refusal | str:
    """A `downgrade`, `cancel` or `resume`; `target` is `None` for Free (a cancel). A string is
    the refusal `no_subscription` (nothing to change) or `use_checkout` (a higher plan)."""
    if state is None or state.status != "active":
        return "no_subscription"
    if rank is None:
        raise ValueError("an active subscription needs its plan's rank")
    quote = quote_change(current_of(state, rank), target, now)
    if isinstance(quote, Quote) and quote.kind in ("buy", "upgrade"):
        return "use_checkout"
    return quote
