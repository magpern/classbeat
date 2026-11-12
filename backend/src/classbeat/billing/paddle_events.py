"""Paddle notifications to the provider-neutral billing events (ADR 0032 sections 1, 3 and 5).

Pure: no I/O, no clock. The webhook route verifies the signature (`paddle_signature`), then:

1. `parse` reads the notification; a type that maps to nothing is a `Skip` here already;
2. `refs` gives the transaction, subscription and customer ids the route resolves the stored
   `Link` by (the user never comes from the payload; `custom_data` is only cross-checked);
3. `map_notification` takes the owner, provider, stored state and link the route resolved and
   returns the neutral event for the writer (`billing.writer.apply`).

One source per event: `transaction.completed` is the only paid source (`paddle_paid`);
`subscription.updated` is `ChangeScheduled` (cancel or resume), state-based and ordered by
Paddle's `occurred_at`; `subscription.canceled` alone is `Ended`, for the stored period (Paddle
nulls `current_billing_period` on a canceled subscription). Every other type is acknowledged
and ignored; refunds and chargebacks by their own reason (section 8), failed payments too (the
plan stays as it is). The neutral `event_id` is Paddle's `evt_...`, the same on every retry.
"""

from __future__ import annotations

from typing import Final
from uuid import UUID

from classbeat.billing import paddle_payload as p
from classbeat.billing.paddle_paid import STARTS, transaction
from classbeat.billing.paddle_subscription import canceled, data_of, updated
from classbeat.billing.paddle_types import (
    OWNER_KEY,
    PROVIDERS,
    Catalog,
    Link,
    Mapped,
    Notification,
    Refs,
    Reject,
    Skip,
    Stored,
)

_MAPPED: Final = frozenset(
    {"transaction.completed", "subscription.updated", "subscription.canceled"}
)
_SKIPPED: Final = {
    "transaction.payment_failed": "payment_failed",
    "adjustment.created": "refund_policy_pending",
    "adjustment.updated": "refund_policy_pending",
}


def parse(body: bytes) -> Notification | Skip | Reject:
    try:
        root = p.load(body)
        kind = p.text(root, "event_type")
        if kind not in _MAPPED:
            return Skip(_SKIPPED.get(kind, "event_type_ignored"))
        p.obj(root, "data")
        return Notification(root, kind, p.event_id(root, "event_id"), p.moment(root, "occurred_at"))
    except p.PayloadError as refused:
        return Reject(refused.reason)


def refs(n: Notification) -> Refs | Reject:
    try:
        data = data_of(n)
        custom = p.custom_value(data, OWNER_KEY)
        customer = p.opt_ident(data, "customer_id")
        if n.event_type == "transaction.completed":
            return Refs(p.ident(data, "id"), p.opt_ident(data, "subscription_id"), customer, custom)
        return Refs(None, p.ident(data, "id"), customer, custom)
    except p.PayloadError as refused:
        return Reject(refused.reason)


def _differs(stored: str | None, notified: str | None) -> bool:
    return stored is not None and notified is not None and stored != notified


def _cross_check(n: Notification, r: Refs, owner_id: UUID, link: Link, provider: str) -> bool:
    """Whether the notification agrees with the link the route resolved and its owner."""
    if link.provider != provider or _differs(link.customer_ref, r.customer_ref):
        return False
    if _differs(link.subscription_ref, r.subscription_ref):
        return False
    if r.custom_owner is not None:
        try:
            if UUID(r.custom_owner) != owner_id:
                return False
        except ValueError:
            return False
    if n.event_type != "transaction.completed":  # a subscription's change or end: its own link
        return link.subscription_ref is not None and link.subscription_ref == r.subscription_ref
    origin = data_of(n).get("origin")
    return origin not in STARTS or link.checkout_ref == r.transaction_ref


def map_notification(
    n: Notification, *, owner_id: UUID, provider: str, catalog: Catalog,
    stored: Stored | None, link: Link,
) -> Mapped | Skip | Reject:  # fmt: skip
    """What a notification means for `owner_id`, whose `link` the route resolved from `refs`."""
    if provider not in PROVIDERS:
        raise ValueError("provider must be paddle or fake_paddle")
    try:
        r = refs(n)
        if isinstance(r, Reject):
            return r
        if n.event_type == "transaction.completed" and r.subscription_ref is None:
            return Skip("not_a_subscription")
        if not _cross_check(n, r, owner_id, link, provider):
            return Reject("link_mismatch")
        state = stored.state if stored is not None else None
        if n.event_type == "transaction.completed":
            return transaction(n.event_id, data_of(n), catalog, provider, state)
        if n.event_type == "subscription.updated":
            return updated(n, provider, stored)
        return canceled(n, provider, stored)
    except p.PayloadError as refused:
        return Reject(refused.reason)
