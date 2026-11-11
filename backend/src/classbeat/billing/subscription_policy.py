"""Subscription periods and quotes (ADR 0031 section 5). Pure: no I/O, no clock, no database.

All times are UTC and timezone-aware (period arithmetic: `classbeat.billing.periods`).
Money is an integer in the currency's minor unit and no function returns a negative amount:
nothing is ever paid back.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from classbeat.billing.periods import as_utc, days_in_period, days_left, period_end

QuoteKind = Literal["buy", "upgrade", "downgrade", "cancel", "resume"]
RefusalReason = Literal["price_missing", "currency_mismatch", "not_an_upgrade", "not_a_downgrade"]


@dataclass(frozen=True)
class Offer:
    """A plan as offered now: its rank and its current monthly price (None = not offered)."""

    plan_id: str
    rank: int
    price_minor: int | None
    currency: str | None


@dataclass(frozen=True)
class Current:
    """The stored subscription. `basis_minor` is the full price paid for this period."""

    plan_id: str
    rank: int
    period_start: datetime
    period_end: datetime
    anchor_day: int
    basis_minor: int
    basis_currency: str
    next_plan_id: str | None = None
    cancel_at_end: bool = False


@dataclass(frozen=True)
class Quote:
    """What a change does: the charge now and the state it leaves (the writer stores it)."""

    kind: QuoteKind
    plan_id: str
    amount_minor: int
    currency: str
    period_start: datetime
    period_end: datetime
    anchor_day: int
    basis_minor: int
    next_plan_id: str | None
    cancel_at_end: bool


@dataclass(frozen=True)
class Refusal:
    reason: RefusalReason


def upgrade_amount(diff_minor: int, left: int, in_period: int) -> int:
    """`diff x left / in_period`, half-up to the minor unit; 0 when `diff` is not positive."""
    if diff_minor <= 0:
        return 0
    return (2 * diff_minor * left + in_period) // (2 * in_period)


def _priced(offer: Offer) -> tuple[int, str] | None:
    if offer.price_minor is None or offer.currency is None:
        return None
    return offer.price_minor, offer.currency


def quote_buy(offer: Offer, now: datetime) -> Quote | Refusal:
    """Free (or an ended subscription) to a paid plan: a new period from now, full price."""
    priced = _priced(offer)
    if priced is None:
        return Refusal("price_missing")
    price, currency = priced
    start = as_utc(now)
    return Quote(
        "buy", offer.plan_id, price, currency, start, period_end(start, start.day),
        start.day, price, None, False,
    )  # fmt: skip


def _unchanged(kind: QuoteKind, cur: Current, *, next_plan_id: str | None, cancel: bool) -> Quote:
    return Quote(
        kind, cur.plan_id, 0, cur.basis_currency, cur.period_start, cur.period_end,
        cur.anchor_day, cur.basis_minor, next_plan_id, cancel,
    )  # fmt: skip


def quote_upgrade(cur: Current, offer: Offer, now: datetime) -> Quote | Refusal:
    """A higher rank at once: the prorated difference to the basis, the period end kept.

    A new price at or below the basis still applies, at amount 0. A scheduled change clears.
    """
    if offer.rank <= cur.rank:
        return Refusal("not_an_upgrade")
    priced = _priced(offer)
    if priced is None:
        return Refusal("price_missing")
    price, currency = priced
    if currency != cur.basis_currency:
        return Refusal("currency_mismatch")
    amount = upgrade_amount(
        price - cur.basis_minor,
        days_left(cur.period_end, now),
        days_in_period(cur.period_start, cur.period_end),
    )
    return Quote(
        "upgrade", offer.plan_id, amount, currency, cur.period_start, cur.period_end,
        cur.anchor_day, price, None, False,
    )  # fmt: skip


def quote_downgrade(cur: Current, offer: Offer) -> Quote | Refusal:
    """A lower rank, scheduled for the period end; nothing changes or is charged now."""
    if offer.rank >= cur.rank:
        return Refusal("not_a_downgrade")
    priced = _priced(offer)
    if priced is None:
        return Refusal("price_missing")
    if priced[1] != cur.basis_currency:
        return Refusal("currency_mismatch")
    return _unchanged("downgrade", cur, next_plan_id=offer.plan_id, cancel=False)


def quote_cancel(cur: Current) -> Quote:
    """Stop at the period end; no refund, no charge."""
    return _unchanged("cancel", cur, next_plan_id=None, cancel=True)


def quote_resume(cur: Current) -> Quote:
    """Choosing the current plan again clears a scheduled downgrade or cancel at amount 0."""
    return _unchanged("resume", cur, next_plan_id=None, cancel=False)


def quote_change(cur: Current, target: Offer | None, now: datetime) -> Quote | Refusal:
    """Route a plan choice: `None` is cancel, the same plan resumes, otherwise by rank."""
    if target is None:
        return quote_cancel(cur)
    if target.plan_id == cur.plan_id:
        return quote_resume(cur)
    if target.rank > cur.rank:
        return quote_upgrade(cur, target, now)
    return quote_downgrade(cur, target)
