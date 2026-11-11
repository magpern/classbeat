"""What applying a billing event does to a stored subscription (ADR 0031 section 3).

Pure: no I/O, no clock, no database. The writer (`classbeat.billing.writer`) loads the stored
`State`, inserts the event row (a conflict on `(provider, event_id)` means it was already seen)
and runs `decide`; the database part only carries out the `Decision`. Keeping the rules here
lets a provider the CHECK does not allow yet (Paddle in M7b) be unit-tested on an in-memory
state.

Rules, in this order:

1. A seen event is a `Duplicate`: nothing changes, under any owner.
2. A provider other than the active subscription's is refused (`provider_conflict`): a Dummy
   `started` never lands on a non-Dummy subscription.
3. An event that begins before the stored period is `Ignored` (`stale`) and recorded.
4. An event that does not fit the stored period (a `renewed` that does not start at its end, a
   change or end for another period, anything on no subscription) is refused (`out_of_order`):
   the writer records nothing, so a real provider's retry can succeed once the gap is filled.
5. A paid event whose amount or currency differs from the writer's own recomputation is refused
   (`amount_mismatch`).
6. A period that is not one month from its start by the anchor is refused (`period_mismatch`).
7. A `renewed` for a cancelled subscription (`cancelled`) or for a plan other than the stored
   plan or its scheduled `next_plan_id` (`plan_mismatch`) is refused.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from classbeat.billing.periods import as_utc, period_end
from classbeat.billing.provider import (
    BillingEvent,
    ChangeScheduled,
    Ended,
    Renewed,
    Started,
    Upgraded,
)


@dataclass(frozen=True)
class State:
    """The stored subscription as the rules see it."""

    provider: str
    plan_id: str
    period_start: datetime
    period_end: datetime
    anchor_day: int
    basis_minor: int
    basis_currency: str
    status: str = "active"
    next_plan_id: str | None = None
    cancel_at_end: bool = False


def _evolve(state: State, **changes: object) -> State:
    """A copy of `state` with `changes` (a plain constructor call, not `dataclasses.replace`)."""
    return State(**{**vars(state), **changes})  # pyright: ignore[reportArgumentType]


@dataclass(frozen=True)
class Expected:
    """What the writer computed for a paid event from the stored state and the current prices."""

    amount_minor: int
    currency: str
    basis_minor: int


@dataclass(frozen=True)
class Grant:
    """The one `subscription` entitlement an applied event inserts: `[starts_at, ends_at)`."""

    plan_id: str
    starts_at: datetime
    ends_at: datetime


@dataclass(frozen=True)
class Duplicate:
    pass


@dataclass(frozen=True)
class Ignored:
    reason: str = "stale"


@dataclass(frozen=True)
class Refused:
    reason: str


@dataclass(frozen=True)
class Applied:
    state: State
    grant: Grant | None


Decision = Duplicate | Ignored | Refused | Applied


def decide(
    state: State | None,
    event: BillingEvent,
    *,
    seen: bool,
    expected: Expected | None = None,
    reset_anchor: bool = False,
) -> Decision:
    """The outcome of `event` on `state`. `expected` is required for a paid event; `reset_anchor`
    is the fast-forward's renewal (ADR 0031 section 6): the anchor becomes the start's UTC day."""
    if seen:
        return Duplicate()
    active = state is not None and state.status == "active"
    if state is not None and active and state.provider != event.provider:
        return Refused("provider_conflict")
    if state is not None and as_utc(event.period_start) < as_utc(state.period_start):
        return Ignored()
    if isinstance(event, Started):
        return _started(state, event, expected)
    if state is None or not active:
        return Refused("out_of_order")
    if isinstance(event, Renewed):
        return _renewed(state, event, expected, reset_anchor)
    if isinstance(event, Upgraded):
        return _upgraded(state, event, expected)
    return _scheduled_or_ended(state, event)


def _paid(event: Started | Renewed | Upgraded, expected: Expected | None) -> bool:
    return (
        expected is not None
        and event.amount_minor == expected.amount_minor
        and event.currency == expected.currency
    )


def _started(state: State | None, event: Started, expected: Expected | None) -> Decision:
    if state is not None and state.status == "active":
        return Refused("already_active")
    if not _paid(event, expected) or expected is None:
        return Refused("amount_mismatch")
    if as_utc(event.period_end) != period_end(event.period_start, event.anchor_day):
        return Refused("period_mismatch")
    new = State(
        event.provider, event.plan_id, as_utc(event.period_start), as_utc(event.period_end),
        event.anchor_day, expected.basis_minor, event.currency,
    )  # fmt: skip
    return Applied(new, Grant(event.plan_id, new.period_start, new.period_end))


def _renewed(
    state: State, event: Renewed, expected: Expected | None, reset_anchor: bool
) -> Decision:
    if as_utc(event.period_start) != as_utc(state.period_end):
        return Refused("out_of_order")
    if state.cancel_at_end:
        return Refused("cancelled")  # a cancel starts nothing: the period end gives `ended`
    if event.plan_id != (state.next_plan_id or state.plan_id):
        return Refused("plan_mismatch")
    if not _paid(event, expected) or expected is None:
        return Refused("amount_mismatch")
    anchor = as_utc(event.period_start).day if reset_anchor else state.anchor_day
    if as_utc(event.period_end) != period_end(event.period_start, anchor):
        return Refused("period_mismatch")
    new = _evolve(
        state, plan_id=event.plan_id, period_start=as_utc(event.period_start),
        period_end=as_utc(event.period_end), anchor_day=anchor, basis_minor=expected.basis_minor,
        basis_currency=event.currency, next_plan_id=None, cancel_at_end=False,
    )  # fmt: skip
    return Applied(new, Grant(event.plan_id, new.period_start, new.period_end))


def _upgraded(state: State, event: Upgraded, expected: Expected | None) -> Decision:
    # The period does not move, and the upgrade happens inside it (ADR 0031 section 5).
    start, end = as_utc(event.period_start), as_utc(event.period_end)
    if end != as_utc(state.period_end) or not state.period_start <= start < state.period_end:
        return Refused("period_mismatch")
    if not _paid(event, expected) or expected is None:
        return Refused("amount_mismatch")
    new = _evolve(
        state, plan_id=event.plan_id, basis_minor=expected.basis_minor,
        basis_currency=event.currency, next_plan_id=None, cancel_at_end=False,
    )  # fmt: skip
    return Applied(new, Grant(event.plan_id, start, end))


def _scheduled_or_ended(state: State, event: ChangeScheduled | Ended) -> Decision:
    if as_utc(event.period_start) != as_utc(state.period_start) or as_utc(
        event.period_end
    ) != as_utc(state.period_end):
        return Refused("out_of_order")
    if isinstance(event, Ended):
        return Applied(_evolve(state, status="ended"), None)
    if event.next_plan_id is not None and (
        event.cancel_at_end or event.next_plan_id == state.plan_id
    ):
        # Never a scheduled plan and a cancel together (the CHECK refuses it as well).
        return Refused("invalid_change")
    new = _evolve(state, next_plan_id=event.next_plan_id, cancel_at_end=event.cancel_at_end)
    return Applied(new, None)
