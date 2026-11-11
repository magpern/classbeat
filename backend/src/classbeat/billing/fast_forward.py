"""What an admin's fast-forward does to a Dummy subscription (ADR 0031 section 6). Pure.

A fast-forward is the test provider's clock: it ends the current period now, so a tester can see
a renewal, a scheduled downgrade or a cancel without waiting a month. It never touches a
subscription of another provider (a real provider's period is its own to end).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from classbeat.billing.apply_policy import Refused, State
from classbeat.billing.dummy import PROVIDER


@dataclass(frozen=True)
class FastForward:
    """`renew`: start the next period (the scheduled plan, else the same one). `end`: the
    subscription stops (a cancel, or its owner may no longer buy)."""

    action: Literal["renew", "end"]
    plan_id: str


def plan_fast_forward(state: State | None, *, eligible: bool) -> FastForward | Refused:
    if state is None or state.status != "active":
        return Refused("no_subscription")
    if state.provider != PROVIDER:
        return Refused("not_dummy")
    if state.cancel_at_end or not eligible:
        return FastForward("end", state.plan_id)
    return FastForward("renew", state.next_plan_id or state.plan_id)
