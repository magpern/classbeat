"""The `billing_links` statements (ADR 0032 section 3, migration 0048). Every one runs on the
maint role's session (the app role only reads the table, for the export) and every write names
`owner_id` (guard 8).

A link is created by the checkout route before the buyer pays (`insert`). The webhook resolves a
notification's user only through it (`find`: by the signed transaction id the checkout stored,
else by the signed subscription id stored when the first payment applied); nothing else in the
payload chooses a row. A customer id maps to at most one user: `billing_customers`, claimed in
the first payment's transaction (`claim_customer`).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Final
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from classbeat.billing.paddle_types import Link, PaddleProvider, Refs

_SELECT = (
    "SELECT id, owner_id, provider, checkout_ref, customer_ref, subscription_ref, changed_at "
    "FROM billing_links WHERE "
)
_INSERT = text(
    "INSERT INTO billing_links (owner_id, provider, checkout_ref) VALUES (:o, :p, :c) "
    "ON CONFLICT (provider, checkout_ref) DO NOTHING"
)
_BY_CHECKOUT = text(_SELECT + "provider = :p AND checkout_ref = :r")
_BY_SUBSCRIPTION = text(_SELECT + "provider = :p AND subscription_ref = :r")
_CURRENT = text(
    _SELECT + "owner_id = :o AND provider = :p AND activated_at IS NOT NULL "
    "ORDER BY activated_at DESC, id LIMIT 1"
)
# One row per Paddle customer, for one owner (its unique key): a concurrent claim by another
# owner waits for this transaction and then finds the row (`ON CONFLICT DO NOTHING`).
_CLAIM = text(
    "INSERT INTO billing_customers (provider, customer_ref, owner_id) VALUES (:p, :c, :o) "
    "ON CONFLICT (provider, customer_ref) DO NOTHING"
)
_CUSTOMER = text("SELECT owner_id FROM billing_customers WHERE provider = :p AND customer_ref = :c")
# A first payment still on its way: the owner has a checkout link not yet activated and made
# within `PENDING_HOURS`. An abandoned checkout stops counting, so a subscription notification
# with no link turns from 409 (retried for days) into a counted 422.
PENDING_HOURS: Final = 24
_PENDING = text(
    "SELECT 1 FROM billing_links WHERE owner_id = :o AND provider = :p "
    "AND activated_at IS NULL AND created_at > now() - make_interval(hours => :h) LIMIT 1"
)
_SUBSCRIPTION_TAKEN = text(
    "SELECT 1 FROM billing_links WHERE provider = :p AND subscription_ref = :s AND id <> :id "
    "LIMIT 1"
)
_ACTIVATE = text(
    "UPDATE billing_links SET subscription_ref = :s, customer_ref = COALESCE(customer_ref, :c), "
    "activated_at = :now, updated_at = :now WHERE id = :id AND owner_id = :o"
)
# Only ever forward: an older time never replaces a newer one.
_CHANGED = text(
    "UPDATE billing_links SET changed_at = :at, updated_at = :now WHERE id = :id AND owner_id = :o "
    "AND (changed_at IS NULL OR changed_at < :at)"
)


@dataclass(frozen=True)
class LinkRow:
    id: UUID
    owner_id: UUID
    link: Link
    changed_at: datetime | None


def _row(r: Any) -> LinkRow:
    link = Link(r.provider, r.checkout_ref, r.customer_ref, r.subscription_ref)
    return LinkRow(r.id, r.owner_id, link, r.changed_at)


async def insert(session: AsyncSession, owner_id: UUID, provider: str, checkout_ref: str) -> None:
    """The checkout's link; a retried checkout (the same transaction) adds nothing."""
    await session.execute(_INSERT, {"o": owner_id, "p": provider, "c": checkout_ref})


async def find(session: AsyncSession, provider: PaddleProvider, refs: Refs) -> LinkRow | None:
    """The link the signed ids select: the checkout's transaction first, else the subscription."""
    for statement, ref in ((_BY_CHECKOUT, refs.transaction_ref),
                           (_BY_SUBSCRIPTION, refs.subscription_ref)):  # fmt: skip
        if ref is None:
            continue
        row = (await session.execute(statement, {"p": provider, "r": ref})).first()
        if row is not None:
            return _row(row)
    return None


async def current(session: AsyncSession, owner_id: UUID, provider: str) -> LinkRow | None:
    """The owner's newest activated link of `provider`: its current Paddle subscription."""
    row = (await session.execute(_CURRENT, {"o": owner_id, "p": provider})).first()
    return None if row is None else _row(row)


async def claim_customer(
    session: AsyncSession, provider: str, customer_ref: str, owner_id: UUID
) -> bool:
    """Map `customer_ref` to `owner_id` in this transaction; `False` when another owner holds it
    (the unique key decides, so two concurrent first payments cannot both win)."""
    params = {"p": provider, "c": customer_ref, "o": owner_id}
    await session.execute(_CLAIM, params)
    holder = (await session.execute(_CUSTOMER, params)).scalar_one_or_none()
    return holder == owner_id


async def payment_pending(
    session: AsyncSession, provider: str, customer_ref: str | None, custom_owner: str | None
) -> bool:
    """Whether a subscription notification with no link yet may belong to a first payment still
    on its way (ADR 0032 section 3: 409, Paddle retries) because its checkout is less than
    `PENDING_HOURS` old. Read only: the candidate owner is the
    customer's (`billing_customers`) or the `custom_data` cross-check value, and it decides
    nothing but 409 against 422; no row is chosen for a write."""
    owner: UUID | None = None
    if customer_ref is not None:
        found = await session.execute(_CUSTOMER, {"p": provider, "c": customer_ref})
        owner = found.scalar_one_or_none()
    if owner is None and custom_owner is not None:
        try:
            owner = UUID(custom_owner)
        except ValueError:
            return False
    if owner is None:
        return False
    params = {"o": owner, "p": provider, "h": PENDING_HOURS}
    return (await session.execute(_PENDING, params)).first() is not None


async def subscription_taken(
    session: AsyncSession, provider: str, subscription_ref: str, link_id: UUID
) -> bool:
    params = {"p": provider, "s": subscription_ref, "id": link_id}
    return (await session.execute(_SUBSCRIPTION_TAKEN, params)).first() is not None


async def activate(
    session: AsyncSession, row: LinkRow, subscription_ref: str, customer_ref: str | None,
    now: datetime,
) -> None:  # fmt: skip
    """The first payment applied: the link now names its subscription and customer."""
    await session.execute(
        _ACTIVATE,
        {"s": subscription_ref, "c": customer_ref, "now": now, "id": row.id, "o": row.owner_id},
    )


async def changed(session: AsyncSession, row: LinkRow, at: datetime, now: datetime) -> None:
    """The link's scheduled change last changed at `at` (Paddle's or classbeat's own time)."""
    await session.execute(_CHANGED, {"at": at, "now": now, "id": row.id, "o": row.owner_id})
