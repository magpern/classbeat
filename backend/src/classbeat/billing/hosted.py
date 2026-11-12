"""The billing routes' Paddle side (ADR 0032 sections 2 and 3): build the `PaddleProvider` for a
request, link a hosted checkout to the buyer before they pay, and record classbeat's own change
time on the link. Runs in the routes' `billing_session` (maint); opens no session.

The client is the app's (`app.state.paddle_client`: the `FakePaddleClient` with `fake_paddle`;
`paddle` is refused by the settings until the real client exists).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from classbeat.billing import links_sql
from classbeat.billing.errors import BillingRefused
from classbeat.billing.paddle_catalog import prices_of
from classbeat.billing.paddle_provider import PaddleProvider
from classbeat.billing.paddle_types import PROVIDERS
from classbeat.billing.prices import PlanPrice
from classbeat.billing.provider import BillingEvent, ChangeScheduled
from classbeat.billing.subscription_policy import Quote


async def provider_for(
    app_state: Any, session: AsyncSession, uid: UUID, provider: str, now: datetime,
    prices: dict[str, PlanPrice],
) -> tuple[PaddleProvider, links_sql.LinkRow | None]:  # fmt: skip
    """The request's provider over the app's client, with the owner's current subscription."""
    client = getattr(app_state, "paddle_client", None)
    if provider not in PROVIDERS or client is None:
        raise BillingRefused("billing_unavailable", 503)
    current = await links_sql.current(session, uid, provider)
    made = PaddleProvider(
        client, provider=provider, now=now,  # pyright: ignore[reportArgumentType]
        prices=prices_of({k: v.offer for k, v in prices.items()}),
        subscription_ref=None if current is None else current.link.subscription_ref,
    )  # fmt: skip
    return made, current


async def checkout(
    app_state: Any, session: AsyncSession, uid: UUID, provider: str, quote: Quote, key: str,
    now: datetime, prices: dict[str, PlanPrice],
) -> str | None:  # fmt: skip
    """A `buy`: the hosted page's URL, its transaction linked to `uid` first. An `upgrade`:
    Paddle charges the proration and notifies; `None`."""
    paddle, current = await provider_for(app_state, session, uid, provider, now, prices)
    if quote.kind == "upgrade" and current is None:
        raise BillingRefused("no_subscription")
    made = await paddle.checkout(quote, owner_id=uid, idempotency_key=key)
    if made.checkout_ref is not None:
        await links_sql.insert(session, uid, provider, made.checkout_ref)
    return made.url


async def change(
    app_state: Any, session: AsyncSession, uid: UUID, provider: str, quote: Quote, key: str,
    now: datetime, prices: dict[str, PlanPrice],
) -> tuple[BillingEvent, links_sql.LinkRow]:  # fmt: skip
    """Tell Paddle; the `ChangeScheduled` classbeat records now, and the link to time it on."""
    paddle, current = await provider_for(app_state, session, uid, provider, now, prices)
    if current is None:
        raise BillingRefused("no_subscription")
    return await paddle.schedule_change(quote, owner_id=uid, idempotency_key=key), current


async def record_change(
    session: AsyncSession, event: BillingEvent, current: links_sql.LinkRow, now: datetime
) -> None:
    """classbeat's own change time, so an older `subscription.updated` is `stale_change`."""
    if isinstance(event, ChangeScheduled) and event.occurred_at is not None:
        await links_sql.changed(session, current, event.occurred_at, now)
