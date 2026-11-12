"""The Paddle mapping's inputs and outcomes (ADR 0032 sections 1, 3 and 5). Pure: types only.

Who a notification is for never comes from the payload: the route resolves a `Link` (the
`billing_links` row created at checkout) from the `Refs` of the notification and passes the
owner it names. `custom_data` is only cross-checked against it.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Final, Literal

from classbeat.billing.apply_policy import State
from classbeat.billing.provider import BillingEvent
from classbeat.billing.subscription_policy import Offer

PaddleProvider = Literal["paddle", "fake_paddle"]
PROVIDERS: Final = frozenset({"paddle", "fake_paddle"})  # the fake is never stored as `paddle`
OWNER_KEY: Final = "classbeat_owner_id"  # the `custom_data` key our checkout sets (cross-check)


@dataclass(frozen=True)
class Catalog:
    """Paddle price id to classbeat plan id, and each plan's current offer (the policy's)."""

    plans: Mapping[str, str]
    offers: Mapping[str, Offer]


@dataclass(frozen=True)
class Notification:
    """A verified notification's JSON root, its type, `evt_` id and `occurred_at`."""

    root: Mapping[str, object]
    event_type: str
    event_id: str
    occurred_at: datetime


@dataclass(frozen=True)
class Refs:
    """The ids the route resolves the link by. `custom_owner` is only for the cross-check."""

    transaction_ref: str | None
    subscription_ref: str | None
    customer_ref: str | None
    custom_owner: str | None


@dataclass(frozen=True)
class Link:
    """The stored `billing_links` row the route resolved (its `owner_id` is passed apart)."""

    provider: PaddleProvider
    checkout_ref: str | None
    customer_ref: str | None
    subscription_ref: str | None


@dataclass(frozen=True)
class Stored:
    """The owner's stored subscription and when its scheduled change last changed (`None`:
    never by a provider notification)."""

    state: State
    changed_at: datetime | None = None


@dataclass(frozen=True)
class Mapped:
    """A neutral event for the writer, and the Paddle subscription it belongs to (for the link)."""

    event: BillingEvent
    subscription_ref: str


@dataclass(frozen=True)
class Skip:
    """Acknowledged and ignored: no event is written. `changed_at` is set only for a newer
    `subscription.updated` that changes nothing: the route stores it as the last change time, so
    an older notification delivered later is `stale_change` (ADR 0032 section 1)."""

    reason: str
    changed_at: datetime | None = None


@dataclass(frozen=True)
class Reject:
    """Refused, nothing written, and no retry can fix it (ADR 0032 section 3: a 422).
    `reason` is a fixed code, never a value from the body."""

    reason: str
