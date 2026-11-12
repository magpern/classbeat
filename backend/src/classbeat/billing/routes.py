"""The user's billing API (ADR 0031 sections 3, 5 and 6). Signed in; the POSTs pass the CSRF and
origin checks of ``classbeat.auth``.

- ``GET /api/account/billing``: the subscription and every paid plan's price with what choosing
  it would do (the charge now), logged as the ``prices`` action.
- ``POST /api/account/billing/checkout {plan, expected_amount_minor}`` with an ``Idempotency-Key``
  header: a buy or an upgrade. 409 ``quote_changed`` when the price moved since the page showed it.
- ``POST /api/account/billing/change {plan}`` (a plan id, ``free`` for a cancel, the current plan
  to resume): a downgrade, a cancel or a resume, scheduled for the period end.

**Which provider** (ADR 0032 section 4): a checkout uses ``billing_provider``. Dummy completes it
at once. A Paddle provider (``fake_paddle``; ``paddle`` is refused until its client exists) links
a hosted checkout to the buyer (``billing.hosted``) and answers its ``checkout_url``, or asks
Paddle for an upgrade; the signed notification completes either (``billing.webhook``). Over an
active Dummy subscription a Paddle checkout is a ``buy`` that replaces it. A change goes through
the stored subscription's own provider.

**Who.** Only a stored admin or an active tester (``billing.access``). Every other caller gets
403 ``billing_closed`` on the app role's session, before any maint connection, log row or price
exists, and sees no price. The check runs again on the maint session inside the write transaction.

The only module that builds a ``Buyer`` or calls ``billing_session`` (an AST guard checks it).
"""

from __future__ import annotations

import re
import secrets
from datetime import datetime
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from classbeat import auth
from classbeat.admin.db import AdminDb, Buyer, billing_session
from classbeat.billing import hosted, writer, writer_sql
from classbeat.billing.access import may_buy
from classbeat.billing.choices import FREE_PLAN, change_quote, checkout_quote
from classbeat.billing.dummy import PROVIDER, DummyProvider, event_id
from classbeat.billing.errors import BillingRefused
from classbeat.billing.paddle_provider import change_event_id
from classbeat.billing.prices import PlanPrice, read_prices
from classbeat.billing.subscription_policy import Quote, Refusal
from classbeat.billing.views import NO_STORE, BillingOut, as_seen_by, rank_of, snapshot
from classbeat.config import Settings
from classbeat.context import UserContext
from classbeat.db import user_session

router = APIRouter(prefix="/api/account/billing", tags=["billing"])

_NOW = text("SELECT now()")
_KEY = re.compile(r"^[A-Za-z0-9_.:-]{8,64}$")


class CheckoutIn(BaseModel):
    plan: str = Field(min_length=1, max_length=64)
    expected_amount_minor: int = Field(ge=0)


class ChangeIn(BaseModel):
    plan: str = Field(min_length=1, max_length=64)


def _refusal(reason: str, status: int) -> JSONResponse:
    return JSONResponse({"detail": {"reason": reason}}, status_code=status, headers=NO_STORE)


async def _gate(
    request: Request, ctx: Annotated[UserContext, Depends(auth.require_user)]
) -> tuple[AdminDb, Buyer]:
    """The app role's check first: a caller who may not buy never reaches the maint role."""
    settings: Settings = request.app.state.settings
    async with user_session(request.app.state.session_factory, ctx) as session:
        allowed = await may_buy(session, ctx.user_id, billing_enabled=settings.billing_enabled)
    if not allowed:
        raise BillingRefused("billing_closed", 403)
    db: AdminDb | None = getattr(request.app.state, "admin_db", None)
    if db is None:
        raise BillingRefused("billing_unavailable", 503)
    rid = getattr(request.state, "request_id", None) or secrets.token_hex(16)
    return db, Buyer(ctx.user_id, str(rid))


async def _now(session: AsyncSession) -> datetime:
    return (await session.execute(_NOW)).scalar_one()


async def _recheck(session: AsyncSession, buyer: Buyer, settings: Settings) -> None:
    """The same check on the maint session, in the write transaction (a time-of-check gap)."""
    if not await may_buy(session, buyer.user_id, billing_enabled=settings.billing_enabled):
        raise BillingRefused("billing_closed", 403)


def _key(value: str | None) -> str:
    if value is None or _KEY.fullmatch(value) is None:
        raise BillingRefused("idempotency_key_invalid", 400)
    return value


async def _checkout_quote(
    session: AsyncSession, uid: UUID, body: CheckoutIn, now: datetime, provider: str
) -> tuple[Quote, dict[str, PlanPrice]]:
    stored = await writer_sql.load_state(session, uid)
    if (
        stored is not None
        and stored.status == "active"
        and stored.provider not in (provider, "dummy")
    ):
        raise BillingRefused("provider_conflict")  # a Paddle subscription changes through Paddle
    state = as_seen_by(stored, provider)
    prices = await read_prices(session)
    plan = prices.get(body.plan)
    if plan is None or plan.offer.plan_id == FREE_PLAN:
        raise BillingRefused("unknown_plan", 422)
    quote = checkout_quote(state, rank_of(state, prices), plan.offer, now)
    if isinstance(quote, Refusal):
        raise BillingRefused(quote.reason)
    if isinstance(quote, str):
        raise BillingRefused(quote)
    if quote.amount_minor != body.expected_amount_minor:
        raise BillingRefused("quote_changed")
    return quote, prices


@router.get("", response_model=BillingOut)
async def get_billing(
    request: Request,
    gate: Annotated[tuple[AdminDb, Buyer], Depends(_gate)],
) -> Any:
    db, buyer = gate
    settings: Settings = request.app.state.settings
    async with billing_session(db, buyer, "prices") as session:
        await _recheck(session, buyer, settings)
        now = await _now(session)
        return await snapshot(session, buyer.user_id, now, provider=settings.billing_provider)


@router.post("/checkout", response_model=BillingOut)
async def checkout(
    body: CheckoutIn,
    request: Request,
    gate: Annotated[tuple[AdminDb, Buyer], Depends(_gate)],
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> Any:
    """Dummy completes at once; a Paddle provider links a hosted checkout (or asks Paddle for the
    upgrade) and the signed notification completes it (`billing.webhook`)."""
    db, buyer = gate
    key = _key(idempotency_key)
    settings: Settings = request.app.state.settings
    provider, uid, url = settings.billing_provider, buyer.user_id, None
    async with billing_session(db, buyer, "checkout", write=True) as session:
        await _recheck(session, buyer, settings)
        now = await _now(session)
        if provider != PROVIDER:
            quote, prices = await _checkout_quote(session, uid, body, now, provider)
            url = await hosted.checkout(
                request.app.state, session, uid, provider, quote, key, now, prices
            )
        elif not await writer_sql.event_seen(session, PROVIDER, event_id(uid, "checkout", key)):
            quote, _ = await _checkout_quote(session, uid, body, now, provider)
            made = await DummyProvider(now).checkout(quote, owner_id=uid, idempotency_key=key)
            if made.event is not None:
                await writer.apply(session, uid, made.event, now=now)
        return await snapshot(session, uid, now, provider=provider, url=url)


@router.post("/change", response_model=BillingOut)
async def change(
    body: ChangeIn,
    request: Request,
    gate: Annotated[tuple[AdminDb, Buyer], Depends(_gate)],
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> Any:
    """Through the stored subscription's own provider: Dummy, or Paddle (told first)."""
    db, buyer = gate
    key = _key(idempotency_key)
    settings: Settings = request.app.state.settings
    uid: UUID = buyer.user_id
    async with billing_session(db, buyer, "change", write=True) as session:
        await _recheck(session, buyer, settings)
        now = await _now(session)
        state = await writer_sql.load_state(session, uid)
        held = PROVIDER if state is None else state.provider
        ident = event_id(uid, "change", key) if held == PROVIDER else change_event_id(uid, key)
        if not await writer_sql.event_seen(session, held, ident):
            prices = await read_prices(session)
            # Free is a cancel, never a downgrade quote (ADR 0031 section 5).
            target = None
            if body.plan != FREE_PLAN:
                found = prices.get(body.plan)
                if found is None:
                    raise BillingRefused("unknown_plan", 422)
                target = found.offer
            quote = change_quote(state, rank_of(state, prices), target, now)
            if isinstance(quote, Refusal):
                raise BillingRefused(quote.reason)
            if isinstance(quote, str):
                raise BillingRefused(quote)
            if held == PROVIDER:
                event = await DummyProvider(now).schedule_change(
                    quote, owner_id=uid, idempotency_key=key
                )
                await writer.apply(session, uid, event, now=now)
            else:
                args = (request.app.state, session, uid, held, quote, key, now, prices)
                event, current = await hosted.change(*args)
                await writer.apply(session, uid, event, now=now)
                await hosted.record_change(session, event, current, now)
        return await snapshot(session, uid, now, provider=settings.billing_provider)


async def refusal_response(_request: Request, exc: Exception) -> Response:
    """The app's handler for ``BillingRefused`` (registered in ``classbeat.web``)."""
    if not isinstance(exc, BillingRefused):
        raise exc
    return _refusal(exc.reason, exc.status)
