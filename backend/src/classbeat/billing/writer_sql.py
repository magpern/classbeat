"""The writer's statements (ADR 0031 section 3). Every one names `owner_id` (guard 8: no query
leans on a policy for the owner), and every one runs on the maint role's session: the app role
cannot insert a subscription entitlement, a billing event or read a price (migration 0046).
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from classbeat import auth
from classbeat.billing.apply_policy import Grant, State
from classbeat.billing.provider import BillingEvent

_STATE = text(
    "SELECT provider, plan_id, period_start, period_end, anchor_day, basis_minor, "
    "basis_currency, status, next_plan_id, cancel_at_end FROM subscriptions WHERE owner_id = :o"
)
_SEEN = text("SELECT 1 FROM billing_events WHERE provider = :p AND event_id = :e")
_EVENT = text(
    "INSERT INTO billing_events (owner_id, provider, event_id, kind, plan_id, period_start, "
    "period_end, amount_minor, currency, outcome) "
    "VALUES (:o, :p, :e, :k, :plan, :s, :end, :amt, :cur, :out) "
    "ON CONFLICT (provider, event_id) DO NOTHING RETURNING id"
)
_GRANT = text(
    "INSERT INTO entitlements (owner_id, plan_id, features, source, starts_at, ends_at, "
    "billing_event_id) VALUES (:o, :plan, CAST('{}' AS text[]), 'subscription', :s, :end, :ev)"
)
# `revoked_by` stays NULL: no operator revoked them, the user's own Paddle checkout did.
_REVOKE_RUNNING = text(
    "UPDATE entitlements SET revoked_at = :now WHERE owner_id = :o "
    "AND source = 'subscription' AND revoked_at IS NULL AND ends_at > :now"
)
_UPSERT = text(
    "INSERT INTO subscriptions (owner_id, provider, plan_id, anchor_day, period_start, "
    "period_end, basis_minor, basis_currency, next_plan_id, cancel_at_end, status, "
    "last_event_at) VALUES (:o, :p, :plan, :a, :s, :end, :b, :c, :next, :cancel, :st, :now) "
    "ON CONFLICT (owner_id) DO UPDATE SET provider = EXCLUDED.provider, "
    "plan_id = EXCLUDED.plan_id, anchor_day = EXCLUDED.anchor_day, "
    "period_start = EXCLUDED.period_start, period_end = EXCLUDED.period_end, "
    "basis_minor = EXCLUDED.basis_minor, basis_currency = EXCLUDED.basis_currency, "
    "next_plan_id = EXCLUDED.next_plan_id, cancel_at_end = EXCLUDED.cancel_at_end, "
    "status = EXCLUDED.status, last_event_at = EXCLUDED.last_event_at, updated_at = now()"
)


async def lock_owner(session: AsyncSession, owner_id: UUID) -> None:
    """Serialize this owner's billing writes (the lock lives in `classbeat.auth`, guard 8; an
    advisory lock is not allowed on the admin path, ADR 0011 section 5)."""
    await auth.lock_owner_row(session, owner_id)


async def load_state(session: AsyncSession, owner_id: UUID) -> State | None:
    row = (await session.execute(_STATE, {"o": owner_id})).first()
    if row is None:
        return None
    return State(
        row.provider, row.plan_id, row.period_start, row.period_end, row.anchor_day,
        row.basis_minor, row.basis_currency, row.status, row.next_plan_id, row.cancel_at_end,
    )  # fmt: skip


async def event_seen(session: AsyncSession, provider: str, event_id: str) -> bool:
    """Any owner's: an event id is global, so a replay under another owner is a duplicate too."""
    found = await session.execute(_SEEN, {"p": provider, "e": event_id})
    return found.first() is not None


async def insert_event(
    session: AsyncSession, owner_id: UUID, event: BillingEvent, outcome: str
) -> UUID | None:
    """The event row, or `None` when the `(provider, event_id)` pair was inserted meanwhile."""
    plan_id = getattr(event, "plan_id", None)
    row = (
        await session.execute(
            _EVENT,
            {
                "o": owner_id, "p": event.provider, "e": event.event_id, "k": event.kind,
                "plan": plan_id, "s": event.period_start, "end": event.period_end,
                "amt": getattr(event, "amount_minor", 0), "cur": getattr(event, "currency", None),
                "out": outcome,
            },
        )
    ).first()  # fmt: skip
    return None if row is None else row.id


async def insert_grant(
    session: AsyncSession, owner_id: UUID, grant: Grant, event_row: UUID
) -> None:
    await session.execute(
        _GRANT,
        {"o": owner_id, "plan": grant.plan_id, "s": grant.starts_at, "end": grant.ends_at,
         "ev": event_row},
    )  # fmt: skip


async def store_state(session: AsyncSession, owner_id: UUID, state: State, now: datetime) -> None:
    await session.execute(
        _UPSERT,
        {"o": owner_id, "p": state.provider, "plan": state.plan_id, "a": state.anchor_day,
         "s": state.period_start, "end": state.period_end, "b": state.basis_minor,
         "c": state.basis_currency, "next": state.next_plan_id, "cancel": state.cancel_at_end,
         "st": state.status, "now": now},
    )  # fmt: skip


async def revoke_running(session: AsyncSession, owner_id: UUID, now: datetime) -> None:
    """End every subscription entitlement of `owner_id` still running at `now` (a replaced Dummy
    period: after an upgrade that is two rows)."""
    await session.execute(_REVOKE_RUNNING, {"o": owner_id, "now": now})
