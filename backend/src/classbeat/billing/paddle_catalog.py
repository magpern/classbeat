"""Paddle price ids for the policy's plans (ADR 0032 sections 1 and 2). Pure: no I/O.

Until the real client exists (M7b-real, the owner creates the prices in Paddle and their ids
become settings), every plan's Paddle price id is derived from its plan id: `pri_` and the id's
hex, so the fake and the webhook agree with no configuration. The amounts are always the
policy's (`subscription_policy.Offer`): Paddle's notification is checked against them.
"""

from __future__ import annotations

from collections.abc import Mapping

from classbeat.billing.paddle_client import Price
from classbeat.billing.paddle_types import Catalog
from classbeat.billing.subscription_policy import Offer


def price_ref(plan_id: str) -> str:
    return f"pri_{plan_id.encode().hex()[:64]}"


def prices_of(offers: Mapping[str, Offer]) -> dict[str, Price]:
    """The offered plans' Paddle prices, by plan id (a plan without a price is not offered)."""
    return {
        plan_id: Price(price_ref(plan_id), plan_id, offer.price_minor, offer.currency)
        for plan_id, offer in offers.items()
        if offer.price_minor is not None and offer.currency is not None
    }


def catalog_of(offers: Mapping[str, Offer]) -> Catalog:
    """What `paddle_events.map_notification` checks a notified price against."""
    return Catalog({price_ref(plan_id): plan_id for plan_id in offers}, dict(offers))
