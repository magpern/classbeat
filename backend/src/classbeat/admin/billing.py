"""The admin's billing area (ADR 0031 section 6): the Dummy subscriptions and the fast-forward.

- ``GET /api/admin/billing``: the Dummy provider's subscriptions, under their own heading (a real
  provider's would be listed apart, M7b); logged as ``billing.list``.
- ``POST /api/admin/users/{user_id}/billing/fast-forward``: ends the user's current Dummy period
  now (step-up like every admin write) and renews, applies the scheduled plan or ends it; logged
  as ``billing.fast_forward`` with one ``admin_audit`` row. Refused for a subscription of another
  provider, and for none.

Steps of a fast-forward, in one transaction: revoke every subscription entitlement still running
(an upgrade leaves two), set the stored period's end to now, then hand the subscription writer a
``renewed`` (the anchor day resets to today's, ``reset_anchor``) or an ``ended`` event, so the
writer's checks apply to it like to any other event. A user's own billing action writes
``billing_log``, never an ``admin_audit`` row; this is the only admin path that writes billing.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from fastapi import Request
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from classbeat.admin.audit import ACTIONS, NoParams, Target
from classbeat.admin.db import AdminRefused, admin_session
from classbeat.admin.routes import AdminActor, admin_router, require_admin_db
from classbeat.billing import writer, writer_sql
from classbeat.billing.access import may_buy
from classbeat.billing.apply_policy import Refused
from classbeat.billing.dummy import PROVIDER, event_id
from classbeat.billing.errors import BillingRefused
from classbeat.billing.fast_forward import FastForward, plan_fast_forward
from classbeat.billing.periods import period_end
from classbeat.billing.prices import read_prices
from classbeat.billing.provider import BillingEvent, Ended, Renewed

router = admin_router()

# Registered here, not in ``classbeat.admin.audit`` (299 lines; split on touch, #948).
ACTIONS["billing.list"] = NoParams
ACTIONS["billing.fast_forward"] = NoParams

_NOW = text("SELECT now()")
_LIST = text(
    "SELECT owner_id, plan_id, status, period_start, period_end, next_plan_id, cancel_at_end "
    "FROM subscriptions WHERE provider = :p ORDER BY period_end, owner_id LIMIT 200"
)
_REVOKE_RUNNING = text(
    "UPDATE entitlements SET revoked_at = :now, revoked_by = :by WHERE owner_id = :o "
    "AND source = 'subscription' AND revoked_at IS NULL AND ends_at > :now"
)
_END_PERIOD = text(
    "UPDATE subscriptions SET period_end = :now, updated_at = :now "
    "WHERE owner_id = :o AND period_start < :now"
)


class SubscriptionRow(BaseModel):
    user_id: UUID
    plan: str
    status: str
    period_start: datetime
    period_end: datetime
    next_plan: str | None
    cancel_at_end: bool


class DummyListOut(BaseModel):
    provider: str
    subscriptions: list[SubscriptionRow]


class FastForwardOut(BaseModel):
    user_id: UUID
    outcome: str
    plan: str
    period_end: datetime | None


@router.get("/billing", response_model=DummyListOut)
async def list_dummy(request: Request, actor: AdminActor) -> DummyListOut:
    async with admin_session(require_admin_db(request), actor, "billing.list") as session:
        rows = (await session.execute(_LIST, {"p": PROVIDER})).all()
    return DummyListOut(
        provider=PROVIDER,
        subscriptions=[
            SubscriptionRow(
                user_id=r.owner_id, plan=r.plan_id, status=r.status,
                period_start=r.period_start, period_end=r.period_end,
                next_plan=r.next_plan_id, cancel_at_end=r.cancel_at_end,
            )
            for r in rows
        ],
    )  # fmt: skip


async def _event(
    session: AsyncSession, owner_id: UUID, step: FastForward, now: datetime, start: datetime
) -> BillingEvent:
    """The writer's event for the step; ids are digests, so a second fast-forward of the same
    period (a double click) is a duplicate."""
    if step.action == "end":
        return Ended(
            provider=PROVIDER, event_id=event_id(owner_id, "ff_end", start.isoformat()),
            period_start=start, period_end=now,
        )  # fmt: skip
    plan = (await read_prices(session)).get(step.plan_id)
    if plan is None or plan.offer.price_minor is None or plan.offer.currency is None:
        raise BillingRefused("price_missing")
    return Renewed(
        provider=PROVIDER, event_id=event_id(owner_id, "ff_renew", start.isoformat()),
        period_start=now, period_end=period_end(now, now.day), plan_id=step.plan_id,
        amount_minor=plan.offer.price_minor, currency=plan.offer.currency,
    )  # fmt: skip


@router.post("/users/{user_id}/billing/fast-forward", response_model=FastForwardOut)
async def fast_forward(request: Request, actor: AdminActor, user_id: UUID) -> FastForwardOut:
    db = require_admin_db(request)
    async with admin_session(
        db, actor, "billing.fast_forward", write=True, target=Target(user_id=user_id)
    ) as session:
        await writer_sql.lock_owner(session, user_id)
        state = await writer_sql.load_state(session, user_id)
        step = plan_fast_forward(state, eligible=await may_buy(session, user_id))
        if isinstance(step, Refused) or state is None:
            raise AdminRefused(step.reason if isinstance(step, Refused) else "no_subscription")
        now = (await session.execute(_NOW)).scalar_one()
        if now <= state.period_start:
            raise AdminRefused("too_soon")
        await session.execute(_REVOKE_RUNNING, {"o": user_id, "now": now, "by": actor.user_id})
        await session.execute(_END_PERIOD, {"o": user_id, "now": now})
        try:
            event = await _event(session, user_id, step, now, state.period_start)
            await writer.apply(session, user_id, event, now=now, reset_anchor=True)
        except BillingRefused as exc:
            raise AdminRefused(exc.reason, exc.status) from exc
        after = await writer_sql.load_state(session, user_id)
    return FastForwardOut(
        user_id=user_id,
        outcome="renewed" if step.action == "renew" else "ended",
        plan=step.plan_id,
        period_end=after.period_end if after is not None else None,
    )
