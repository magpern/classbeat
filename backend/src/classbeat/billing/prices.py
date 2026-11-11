"""Plan prices, read on the maint role only (ADR 0031 section 7).

This module opens no session: it reads with the one it is given, which is the `billing_session`
the billing routes opened (the only caller allowed) or, in a fast-forward, the admin's
`admin_session`. The app role cannot read a price (migration 0030, guard 2). Only the monthly
price is used; the introduction price stays admin-only data in M7a.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from classbeat.billing.subscription_policy import Offer

_PLANS = text("SELECT id, name, rank, monthly_price_cents, currency FROM plans ORDER BY rank, id")


@dataclass(frozen=True)
class PlanPrice:
    name: str
    offer: Offer


async def read_prices(session: AsyncSession) -> dict[str, PlanPrice]:
    """Every plan with its current monthly price (`None` = not offered), by plan id."""
    rows = (await session.execute(_PLANS)).all()
    return {
        r.id: PlanPrice(r.name, Offer(r.id, r.rank, r.monthly_price_cents, r.currency))
        for r in rows
    }
