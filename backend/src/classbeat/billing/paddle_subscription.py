"""`subscription.updated` and `subscription.canceled` to neutral events (ADR 0032 section 1).

Pure: no I/O. The caller (`paddle_events.map_notification`) has checked the link.

- `subscription.updated` is `ChangeScheduled`, state-based: the notification's scheduled cancel
  (or none, a resume) is compared with the stored one, ordered by Paddle's `occurred_at` against
  the stored last change time. An older notification is `stale_change`; a newer one that changes
  nothing is a `Skip` carrying its time, so the route moves the last change time forward. A
  non-live status (canceled, past_due, paused) is skipped: `subscription.canceled` ends it.
- `subscription.canceled` is `Ended` for the stored period.
"""

from __future__ import annotations

from typing import Final

from classbeat.billing import paddle_payload as p
from classbeat.billing.paddle_types import Mapped, Notification, Reject, Skip, Stored
from classbeat.billing.provider import ChangeScheduled, Ended

_LIVE: Final = frozenset({"active", "trialing"})


def data_of(n: Notification) -> p.Json:
    return p.obj(n.root, "data")


def updated(n: Notification, provider: str, stored: Stored | None) -> Mapped | Skip | Reject:
    data = data_of(n)
    if p.text(data, "status") not in _LIVE:
        return Skip("not_active")  # canceled, past_due, paused: `subscription.canceled` ends it
    change = p.opt_obj(data, "scheduled_change")
    action = None if change is None else change.get("action")
    if action not in (None, "cancel"):
        return Skip("change_not_supported")  # pause: not a classbeat state
    cancel = action == "cancel"
    next_plan = None
    if stored is not None:
        state = stored.state
        if state.status != "active":
            return Skip("no_active_subscription")
        if state.provider != provider:
            return Reject("provider_conflict")
        if stored.changed_at is not None and n.occurred_at <= stored.changed_at:
            return Skip("stale_change")
        if state.cancel_at_end == cancel:
            return Skip("no_change", changed_at=n.occurred_at)
        # Paddle knows nothing of a classbeat downgrade: a resume keeps it, a cancel replaces it.
        next_plan = None if cancel else state.next_plan_id
    span = p.period(data, "current_billing_period")
    if change is not None and p.moment(change, "effective_at") != span.end:
        return Reject("cancel_not_at_period_end")  # no refund: a cancel runs to the period end
    event = ChangeScheduled(
        provider=provider, event_id=n.event_id, period_start=span.start, period_end=span.end,
        next_plan_id=next_plan, cancel_at_end=cancel, occurred_at=n.occurred_at,
    )  # fmt: skip
    return Mapped(event, p.ident(data, "id"))


def canceled(n: Notification, provider: str, stored: Stored | None) -> Mapped | Skip:
    data = data_of(n)
    canceled_at = p.moment(data, "canceled_at")
    state = stored.state if stored is not None else None
    if state is None or state.status != "active" or state.provider != provider:
        return Skip("no_active_subscription")
    if canceled_at < state.period_start:
        return Skip("stale")  # an older subscription's cancel, delivered late
    event = Ended(provider=provider, event_id=n.event_id, period_start=state.period_start,
                  period_end=state.period_end)  # fmt: skip
    return Mapped(event, p.ident(data, "id"))
