"""Which Paddle subscription a notification may act on (ADR 0032 section 3). Pure: no I/O.

The signed transaction or subscription id selects the `billing_links` row; that link names the
owner. An owner can hold several links (a returning buyer's second checkout), but only the newest
activated one is the **current** subscription. A notification about another (older) one never
changes the stored state: its `subscription.updated` or `subscription.canceled` is skipped (a
cancel of a different subscription must not end the current one), and its paid transaction is
refused, so an admin sees money Paddle took for a subscription classbeat no longer serves.
A first payment (`web` or `api`) is not checked here: its link is found by its own checkout.
"""

from __future__ import annotations

from classbeat.billing.paddle_types import Reject, Skip


def gate(
    *, event_type: str, starts: bool, link_subscription: str | None, current: str | None
) -> Skip | Reject | None:
    """`None` when the notification may go on to the mapping; `current` is the owner's current
    subscription id for this provider (`None`: none activated yet)."""
    if event_type == "transaction.completed" and starts:
        return None
    if link_subscription is not None and link_subscription == current:
        return None
    if event_type == "transaction.completed":
        return Reject("other_subscription")
    return Skip("other_subscription")
