"""The Paddle mapping's inputs and outcomes (ADR 0032 section 1). Pure: types only."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Final
from uuid import UUID

from classbeat.billing.provider import BillingEvent
from classbeat.billing.subscription_policy import Offer

PROVIDER: Final = "paddle"
OWNER_KEY: Final = "classbeat_owner_id"  # the `custom_data` key our checkout sets


@dataclass(frozen=True)
class Catalog:
    """Paddle price id to classbeat plan id, and each plan's current offer (the policy's)."""

    plans: Mapping[str, str]
    offers: Mapping[str, Offer]


@dataclass(frozen=True)
class Mapped:
    """A neutral event for the writer, and the Paddle subscription it belongs to."""

    owner_id: UUID
    subscription_id: str
    event: BillingEvent


@dataclass(frozen=True)
class Skip:
    """Acknowledged and ignored: nothing is written."""

    reason: str


@dataclass(frozen=True)
class Reject:
    """Refused: nothing is written. `reason` is a fixed code, never a value from the body."""

    reason: str
