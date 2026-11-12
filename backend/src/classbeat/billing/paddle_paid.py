"""`transaction.completed` to a paid neutral event (ADR 0032 section 1). Pure: no I/O.

The only source of paid events, by the transaction's `origin` and the stored state: `web` or
`api` is `Started` (refused while this provider's subscription is active; a Dummy one is
replaced, ADR 0032 section 5), `subscription_recurring` is `Renewed` and `subscription_update`
(the proration charge of a plan change) is `Upgraded`, both refused without an active
subscription of this provider. Other origins are skipped. The item's price, currency and billing
cycle must agree with the policy's offer; the writer then checks the amount against its quote.
"""

from __future__ import annotations

from typing import Final

from classbeat.billing import paddle_payload as p
from classbeat.billing.apply_policy import State
from classbeat.billing.paddle_types import Catalog, Mapped, Skip
from classbeat.billing.periods import period_end
from classbeat.billing.provider import BillingEvent, Renewed, Started, Upgraded
from classbeat.billing.subscription_policy import Offer

STARTS: Final = frozenset({"web", "api"})
_ORIGINS: Final = STARTS | {"subscription_recurring", "subscription_update"}


def _plan(item: p.Json, catalog: Catalog) -> tuple[str, Offer]:
    """The item's plan, checked against the policy's offer: price, currency, monthly cycle."""
    price = p.obj(item, "price")
    plan_id = catalog.plans.get(p.ident(price, "id"))
    offer = catalog.offers.get(plan_id) if plan_id is not None else None
    if plan_id is None or offer is None:
        raise p.PayloadError("unknown_price")
    if offer.price_minor is None or offer.currency is None:
        raise p.PayloadError("price_missing")
    unit = p.obj(price, "unit_price")
    notified = (p.minor(unit, "amount"), p.currency(unit, "currency_code"))
    if notified != (offer.price_minor, offer.currency):
        raise p.PayloadError("price_mismatch")
    cycle = p.obj(price, "billing_cycle")
    frequency = cycle.get("frequency")
    if cycle.get("interval") != "month" or type(frequency) is not int or frequency != 1:
        raise p.PayloadError("period_mismatch")
    return plan_id, offer


def _by_state(origin: str, provider: str, state: State | None) -> None:
    active = state is not None and state.status == "active"
    if origin in STARTS:
        if active and state is not None and state.provider == provider:
            raise p.PayloadError("already_active")
        return
    if not active or state is None:
        raise p.PayloadError("no_active_subscription")  # permanent: no retry starts one
    if state.provider != provider:
        raise p.PayloadError("provider_conflict")


def transaction(
    event_id: str, data: p.Json, catalog: Catalog, provider: str, state: State | None
) -> Mapped | Skip:
    """Raises `PayloadError` for a refusal. The caller has checked the link."""
    origin = p.text(data, "origin")
    if origin not in _ORIGINS:
        return Skip("origin_ignored")
    _by_state(origin, provider, state)
    sub_ref = p.ident(data, "subscription_id")
    item = p.single_item(data)
    plan_id, offer = _plan(item, catalog)
    if p.currency(data, "currency_code") != offer.currency:
        raise p.PayloadError("price_mismatch")
    totals = p.obj(p.obj(data, "details"), "totals")
    if p.minor(totals, "discount") != 0:
        raise p.PayloadError("discount_not_supported")  # the policy has no discounts
    # Net of tax: Paddle, the merchant of record, adds and remits the tax; the policy's price is
    # the plan price. To be confirmed against the sandbox (ADR 0032, Alternatives).
    paid = {"provider": provider, "event_id": event_id, "plan_id": plan_id,
            "amount_minor": p.minor(totals, "subtotal"), "currency": offer.currency}  # fmt: skip
    if origin == "subscription_update":
        # The charge covers the rest of the period, from the change to the period end.
        span = p.period(p.obj(item, "proration"), "billing_period")
        event: BillingEvent = Upgraded(**paid, period_start=span.start, period_end=span.end)
    elif origin in STARTS:
        span = p.period(data, "billing_period")
        if span.end != period_end(span.start, span.start.day):
            raise p.PayloadError("period_mismatch")
        event = Started(
            **paid, period_start=span.start, period_end=span.end, anchor_day=span.start.day
        )
    else:  # the writer checks a renewal's start and end against the stored period and anchor
        span = p.period(data, "billing_period")
        event = Renewed(**paid, period_start=span.start, period_end=span.end)
    return Mapped(event, sub_ref)
