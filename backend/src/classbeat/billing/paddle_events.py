"""Paddle notifications to the provider-neutral billing events (ADR 0032 section 1).

Pure: no I/O, no clock. The webhook route verifies the signature (`paddle_signature`) first,
loads the owner's stored `State` and hands both here; the writer (`billing.writer.apply`) then
decides what the event does. One source per event, since Paddle sends several notifications for
one purchase:

- `transaction.completed` is the only source of paid events (`paddle_paid`: `Started`,
  `Renewed` or `Upgraded` by its `origin`).
- `subscription.updated` is `ChangeScheduled`: a scheduled `cancel` sets `cancel_at_end`; no
  scheduled change on a stored cancel is a resume. An update that changes neither is skipped.
- `subscription.canceled` is `Ended` for the stored period. Paddle's dunning ends a `past_due`
  subscription by cancelling it, so a final `past_due` arrives as this notification too.
- Every other type (`subscription.created`, `subscription.activated`, ...) is a `Skip`:
  acknowledged, nothing written. Refunds and chargebacks are skipped by their own reason (ADR 0032
  section 8: no refund event exists), as are failed payments (the plan stays as it is).

The neutral `event_id` is Paddle's notification `event_id` (`evt_...`), the same on every retry.
Plans and prices come only from the `Catalog` (classbeat's plans and `subscription_policy`
offers): a price, currency, billing cycle or period that disagrees with it is a `Reject`, and the
writer checks the amount again against its own quote.
"""

from __future__ import annotations

from typing import Final
from uuid import UUID

from classbeat.billing import paddle_payload as p
from classbeat.billing.apply_policy import State
from classbeat.billing.paddle_paid import transaction
from classbeat.billing.paddle_types import OWNER_KEY, PROVIDER, Catalog, Mapped, Reject, Skip
from classbeat.billing.provider import ChangeScheduled, Ended

__all__ = ["Catalog", "Mapped", "Reject", "Skip", "map_notification", "owner_of"]

_SKIPPED: Final = {
    "transaction.payment_failed": "payment_failed",
    "adjustment.created": "refund_policy_pending",
    "adjustment.updated": "refund_policy_pending",
}


def owner_of(body: bytes) -> UUID | Reject:
    """The classbeat user a verified notification is for, so the route can load their state."""
    try:
        return p.owner(p.obj(p.load(body), "data"), OWNER_KEY)
    except p.PayloadError as refused:
        return Reject(refused.reason)


def map_notification(body: bytes, catalog: Catalog, state: State | None) -> Mapped | Skip | Reject:
    """What one verified Paddle notification means, given the owner's stored subscription."""
    try:
        root = p.load(body)
        kind = p.text(root, "event_type")
        if kind not in ("transaction.completed", "subscription.updated", "subscription.canceled"):
            return Skip(_SKIPPED.get(kind, "event_type_ignored"))
        event_id, data = p.ident(root, "event_id"), p.obj(root, "data")
        if kind == "transaction.completed":
            return transaction(event_id, data, catalog)
        if kind == "subscription.updated":
            return _updated(event_id, data, state)
        return _canceled(event_id, data, state)
    except p.PayloadError as refused:
        return Reject(refused.reason)


def _updated(event_id: str, data: p.Json, state: State | None) -> Mapped | Skip:
    change = p.opt_obj(data, "scheduled_change")
    action = None if change is None else change.get("action")
    if action not in (None, "cancel"):
        return Skip("change_not_supported")  # pause and resume-from-pause: not a classbeat state
    cancel = action == "cancel"
    if state is not None and state.status == "active" and state.cancel_at_end == cancel:
        return Skip("no_change")  # a payment-method or address change, or a retry of ours
    span = p.period(data, "current_billing_period")
    if change is not None and p.moment(change, "effective_at") != span.end:
        raise p.PayloadError("cancel_not_at_period_end")  # no refund: a cancel runs to the end
    event = ChangeScheduled(provider=PROVIDER, event_id=event_id, period_start=span.start,
                            period_end=span.end, cancel_at_end=cancel)  # fmt: skip
    return Mapped(p.owner(data, OWNER_KEY), p.ident(data, "id"), event)


def _canceled(event_id: str, data: p.Json, state: State | None) -> Mapped | Skip:
    # Paddle nulls `current_billing_period` on a canceled subscription, so `Ended` names the
    # stored period; a cancel from before that period (an older subscription) changes nothing.
    owner, sub_id = p.owner(data, OWNER_KEY), p.ident(data, "id")
    canceled_at = p.moment(data, "canceled_at")
    if state is None or state.status != "active" or state.provider != PROVIDER:
        return Skip("no_active_subscription")
    if canceled_at < state.period_start:
        return Skip("stale")
    event = Ended(provider=PROVIDER, event_id=event_id, period_start=state.period_start,
                  period_end=state.period_end)  # fmt: skip
    return Mapped(owner, sub_id, event)
