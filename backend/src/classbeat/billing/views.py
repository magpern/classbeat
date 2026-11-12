"""What the billing routes answer (ADR 0031 sections 5 and 6): the subscription and each paid
plan's offer with what choosing it would do now. Moved out of `billing.routes` (#973).

`snapshot` reads with the session it is given (the routes' `billing_session`); it opens none.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from classbeat.billing import writer_sql
from classbeat.billing.apply_policy import State
from classbeat.billing.choices import FREE_PLAN, change_quote, checkout_quote
from classbeat.billing.prices import PlanPrice, read_prices
from classbeat.billing.subscription_policy import Quote, Refusal

NO_STORE = {"Cache-Control": "no-store"}


class SubscriptionOut(BaseModel):
    plan: str
    provider: str
    status: str
    period_start: datetime
    period_end: datetime
    next_plan: str | None
    cancel_at_end: bool


class OfferOut(BaseModel):
    plan: str
    name: str
    price_minor: int | None
    currency: str | None
    action: str | None  # buy, upgrade, downgrade, resume or None when it cannot be chosen
    charge_minor: int | None  # what choosing it charges now (0 for a downgrade or a resume)
    reason: str | None


class BillingOut(BaseModel):
    can_buy: bool
    subscription: SubscriptionOut | None
    offers: list[OfferOut]
    # A Paddle checkout (ADR 0032): the hosted page to pay on; the plan changes when the signed
    # notification lands, so the page waits for it (PB-3).
    checkout_url: str | None = None


def view(state: State | None) -> SubscriptionOut | None:
    if state is None:
        return None
    return SubscriptionOut(
        plan=state.plan_id, provider=state.provider, status=state.status,
        period_start=state.period_start, period_end=state.period_end,
        next_plan=state.next_plan_id, cancel_at_end=state.cancel_at_end,
    )  # fmt: skip


def as_seen_by(state: State | None, provider: str) -> State | None:
    """The state a checkout of `provider` quotes against: a Paddle checkout sees none over a
    Dummy subscription, so it is a `buy` that replaces it (ADR 0032 section 5)."""
    if provider != "dummy" and state is not None and state.provider == "dummy":
        return None
    return state


def rank_of(state: State | None, prices: dict[str, PlanPrice]) -> int | None:
    """The stored plan's rank (the quotes need it for an active subscription)."""
    if state is None or state.plan_id not in prices:
        return None
    return prices[state.plan_id].offer.rank


def offers(
    state: State | None, prices: dict[str, PlanPrice], now: datetime, *, provider: str
) -> list[OfferOut]:
    """`provider` is the one a checkout would use: a Paddle checkout over an active Dummy
    subscription is a `buy` that replaces it (ADR 0032 section 5)."""
    state = as_seen_by(state, provider)
    rank = rank_of(state, prices)
    active = state is not None and state.status == "active"
    out: list[OfferOut] = []
    for plan in prices.values():
        offer = plan.offer
        if plan.offer.plan_id == FREE_PLAN:
            continue
        higher = not active or (rank is not None and offer.rank > rank)
        quote = (
            checkout_quote(state, rank, offer, now)
            if higher
            else change_quote(state, rank, offer, now)
        )
        if isinstance(quote, Quote):
            action, charge, reason = quote.kind, quote.amount_minor, None
        else:
            action, charge = None, None
            reason = quote.reason if isinstance(quote, Refusal) else quote
        out.append(
            OfferOut(
                plan=offer.plan_id, name=plan.name, price_minor=offer.price_minor,
                currency=offer.currency, action=action, charge_minor=charge, reason=reason,
            )
        )  # fmt: skip
    return out


async def snapshot(
    session: AsyncSession, uid: UUID, now: datetime, *, provider: str, url: str | None = None
) -> JSONResponse:
    state = await writer_sql.load_state(session, uid)
    prices = await read_prices(session)
    body = BillingOut(
        can_buy=True, subscription=view(state),
        offers=offers(state, prices, now, provider=provider), checkout_url=url,
    )  # fmt: skip
    return JSONResponse(body.model_dump(mode="json"), headers=NO_STORE)
