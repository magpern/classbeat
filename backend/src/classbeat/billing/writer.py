"""The subscription writer (ADR 0031 section 3): the only code that turns a billing event into
stored state, and it runs on the maint role's session only.

One call is one event in the caller's write transaction:

1. lock the owner's `users` row, so two requests of one owner run one after the other;
2. load the stored state and look the event up (`seen`, under any owner);
3. recompute what the event should have charged from the plan prices and the pure quotes, never
   from the event (a provider's number is checked, not trusted);
4. `apply_policy.decide`; a refusal raises `BillingRefused` and records nothing, so the
   transaction rolls back and a retry can succeed;
5. insert the event row (`ON CONFLICT DO NOTHING` closes the race with another owner's request
   for the same id), then the subscription entitlement and the stored state in the same
   transaction, so an event and its effect land together or not at all.

The caller (`billing.routes`, `admin.billing`) passes the transaction's `now`; nothing here
reads a clock.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from classbeat.billing import writer_sql as sql
from classbeat.billing.apply_policy import (
    Applied,
    Duplicate,
    Expected,
    Ignored,
    Refused,
    State,
    decide,
)
from classbeat.billing.choices import current_of, in_period
from classbeat.billing.errors import BillingRefused
from classbeat.billing.prices import PlanPrice, read_prices
from classbeat.billing.provider import BillingEvent, Renewed, Started, Upgraded
from classbeat.billing.subscription_policy import Quote, quote_buy, quote_upgrade


def _expected(
    event: BillingEvent, state: State | None, prices: dict[str, PlanPrice], now: datetime
) -> Expected | None:
    """What a paid event should have charged, or `None` for an event that charges nothing (or
    one on no subscription, which `decide` refuses as out of order before it looks here)."""
    if not isinstance(event, Started | Renewed | Upgraded):
        return None
    plan = prices.get(event.plan_id)
    if plan is None:
        raise BillingRefused("unknown_plan", 422)
    if isinstance(event, Started | Renewed):
        quote = quote_buy(plan.offer, event.period_start)
    else:
        if state is None or state.status != "active":
            return None
        if not in_period(state, now):
            raise BillingRefused("outside_period")
        stored = prices.get(state.plan_id)
        if stored is None:
            raise BillingRefused("unknown_plan", 422)
        quote = quote_upgrade(current_of(state, stored.offer.rank), plan.offer, now)
    if not isinstance(quote, Quote):
        raise BillingRefused(quote.reason)
    return Expected(quote.amount_minor, quote.currency, quote.basis_minor)


async def apply(
    session: AsyncSession,
    owner_id: UUID,
    event: BillingEvent,
    *,
    now: datetime,
    reset_anchor: bool = False,
) -> Applied | Ignored | Duplicate:
    """Apply `event` for `owner_id`; raise `BillingRefused` for a refusal (nothing is kept)."""
    await sql.lock_owner(session, owner_id)
    state = await sql.load_state(session, owner_id)
    seen = await sql.event_seen(session, event.provider, event.event_id)
    prices = {} if seen else await read_prices(session)
    expected = None if seen else _expected(event, state, prices, now)
    decision = decide(state, event, seen=seen, expected=expected, reset_anchor=reset_anchor)
    if isinstance(decision, Refused):
        raise BillingRefused(decision.reason)
    if isinstance(decision, Duplicate):
        return decision
    outcome = "applied" if isinstance(decision, Applied) else "ignored"
    event_row = await sql.insert_event(session, owner_id, event, outcome)
    if event_row is None:
        return Duplicate()  # another request stored the same event id first
    if isinstance(decision, Applied):
        if decision.grant is not None:
            await sql.insert_grant(session, owner_id, decision.grant, event_row)
        await sql.store_state(session, owner_id, decision.state, now)
    return decision
