"""The fence (ADR 0005 section 3): every write a worker makes for a job goes through
``fenced_session``.

``fenced_session(factory, lease)`` is a ``user_session`` as the job's owner whose first statement
is the fence::

    UPDATE jobs SET updated_at = now()
     WHERE id = :job_id AND owner_id = :owner_id AND lease_version = :v AND state = 'running'
    RETURNING cancel_requested_at, next_seq

0 rows raises ``LeaseLost`` and rolls the transaction back, so a worker whose lease expired (the
reaper bumped the token) or was handed back cannot add an event, a revision, an ``agent_runs``
row or an external-operation outcome, nor move the job. The fence's row lock is held until the
commit, so the reaper (which skips locked rows) cannot bump the token in between.

Only ``classbeat.jobs``, ``classbeat.connections`` and ``classbeat.auth`` may call
``user_session`` directly (``tests/test_session_guard.py``); worker and agent code write through
this helper.
"""

import json
import uuid
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any, Final
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from classbeat.context import UserContext
from classbeat.db import user_session
from classbeat.jobs.errors import LeaseLost

Factory = async_sessionmaker[AsyncSession]

# ADR 0005 section 6 (the ``job_events.kind`` check of migration 0004).
EVENT_KINDS: Final = frozenset(
    {
        "queued",
        "claimed",
        "stage_started",
        "tool_called",
        "revision_created",
        "waiting_for_approval",
        "approved",
        "retry_scheduled",
        "cancel_requested",
        "cancelled",
        "completed",
        "failed",
        "expired",
        "requeued",
        "external_operation",
    }
)
# Extra seconds a lease must outlive a provider call by (ADR 0005 section 3).
CALL_LEASE_MARGIN_SECONDS: Final = 30

FENCE = text(
    "UPDATE jobs SET updated_at = now() "
    "WHERE id = :job_id AND owner_id = :owner_id AND lease_version = :v AND state = 'running' "
    "RETURNING cancel_requested_at, next_seq"
)
NEXT_SEQ = text(
    "UPDATE jobs SET next_seq = next_seq + 1 "
    "WHERE id = :job_id AND owner_id = :owner_id AND lease_version = :v AND state = 'running' "
    "RETURNING next_seq"
)
INSERT_EVENT = text(
    "INSERT INTO job_events (id, owner_id, job_id, seq, kind, payload, lease_version) "
    "VALUES (:id, :owner_id, :job_id, :seq, :kind, CAST(:payload AS jsonb), :v)"
)
# GREATEST: never shortens an extension made for a provider call.
EXTEND = text(
    "UPDATE jobs SET lease_expires_at = GREATEST(lease_expires_at, "
    "now() + make_interval(secs => :seconds)), heartbeat_at = CASE WHEN :beat THEN now() "
    "ELSE heartbeat_at END "
    "WHERE id = :job_id AND owner_id = :owner_id AND lease_version = :v AND state = 'running' "
    "RETURNING cancel_requested_at"
)


@dataclass(frozen=True, slots=True)
class Lease:
    """What a worker holds for one run: the job, its fencing token and the owner's context
    (issued by ``classbeat.jobs.issuer`` from the claimed row)."""

    job_id: UUID
    lease_version: int
    ctx: UserContext

    @property
    def owner_id(self) -> UUID:
        return self.ctx.user_id

    def params(self) -> dict[str, Any]:
        return {"job_id": self.job_id, "owner_id": self.ctx.user_id, "v": self.lease_version}


def _payload(payload: Mapping[str, Any] | None) -> str:
    """Flat JSON of enum values, ids, counts and timestamps (ADR 0005 section 6). J4 adds a
    model per kind; here nested values and texts longer than 100 characters are refused."""
    values = dict(payload or {})
    for key, value in values.items():
        if not isinstance(key, str) or not (
            value is None or isinstance(value, bool | int | float | str | UUID)
        ):
            raise ValueError("an event payload holds only flat scalar values")
        if isinstance(value, str) and len(value) > 100:
            raise ValueError("an event payload value is too long")
    return json.dumps({k: str(v) if isinstance(v, UUID) else v for k, v in values.items()})


async def insert_event(
    db: AsyncSession,
    lease: Lease,
    seq: int,
    kind: str,
    payload: Mapping[str, Any] | None = None,
) -> None:
    """Insert the event with ``seq`` (taken from ``jobs.next_seq`` by the caller's fenced
    update), recording the token the writer held."""
    if kind not in EVENT_KINDS:
        raise ValueError("unknown event kind")
    await db.execute(
        INSERT_EVENT,
        {
            **lease.params(),
            "id": uuid.uuid4(),
            "seq": seq,
            "kind": kind,
            "payload": _payload(payload),
        },
    )


class FencedSession:
    """The open fenced transaction. ``db`` is the owner's session; ``cancel_requested`` is what
    the fence read (the row is locked, so it holds until the commit)."""

    def __init__(self, db: AsyncSession, lease: Lease, cancel_requested: bool) -> None:
        self.db = db
        self.lease = lease
        self.cancel_requested = cancel_requested

    async def event(self, kind: str, payload: Mapping[str, Any] | None = None) -> int:
        """Append an event to the running job; returns its ``seq``."""
        seq = (await self.db.execute(NEXT_SEQ, self.lease.params())).scalar_one_or_none()
        if seq is None:
            raise LeaseLost
        await insert_event(self.db, self.lease, seq, kind, payload)
        return seq

    async def extend_lease(self, seconds: float, *, heartbeat: bool = False) -> bool:
        """``lease_expires_at = GREATEST(lease_expires_at, now() + seconds)``; returns whether a
        cancel is requested."""
        row = (
            await self.db.execute(
                EXTEND, {**self.lease.params(), "seconds": float(seconds), "beat": heartbeat}
            )
        ).first()
        if row is None:
            raise LeaseLost
        self.cancel_requested = row.cancel_requested_at is not None
        return self.cancel_requested

    async def extend_lease_for_call(self, call_timeout_seconds: float) -> None:
        """For the transaction that writes an external operation's ``pending`` row (ADR 0005
        section 3, X1): the lease outlives the call's timeout by 30 s."""
        if call_timeout_seconds <= 0:
            raise ValueError("a provider call needs a positive timeout")
        await self.extend_lease(call_timeout_seconds + CALL_LEASE_MARGIN_SECONDS)


@asynccontextmanager
async def fenced_session(factory: Factory, lease: Lease) -> AsyncIterator[FencedSession]:
    """One transaction as the job's owner, fenced on ``lease``; ``LeaseLost`` if it is gone."""
    async with user_session(factory, lease.ctx) as db:
        row = (await db.execute(FENCE, lease.params())).first()
        if row is None:
            raise LeaseLost
        yield FencedSession(db, lease, row.cancel_requested_at is not None)
