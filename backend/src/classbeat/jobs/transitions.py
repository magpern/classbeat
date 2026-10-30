"""The worker's state transitions (ADR 0005 sections 2 to 4), each inside a fenced transaction.

Every transition out of ``running`` is one ``UPDATE ... WHERE id AND owner_id AND lease_version
AND state = 'running'`` that also takes the next ``seq`` and inserts its event; the fence has
locked the row already, so 0 rows only happens if the lease is gone (``LeaseLost``). A requeue of
a job with a cancel requested ends it ``cancelled`` instead (cancel-on-requeue). Every requeue
raises ``lease_version``, so nothing the old holder still tries can pass the fence.

The user's transitions (cancel, approve) are in ``classbeat.jobs.user``.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import text

from classbeat.jobs.errors import RETRIED_ONCE, RETRYABLE, LeaseLost
from classbeat.jobs.fence import FencedSession, insert_event

# Each statement spells the fence out: WHERE id, owner_id, lease_version and state = 'running'.
_TERMINAL = text(
    "UPDATE jobs SET state = :state, error_category = :category, finished_at = now(), "
    "next_seq = next_seq + 1, updated_at = now(), "
    "leased_by = NULL, lease_expires_at = NULL, heartbeat_at = NULL "
    "WHERE id = :job_id AND owner_id = :owner_id AND lease_version = :v AND state = 'running' "
    "RETURNING next_seq"
)
_REQUEUE = text(
    "UPDATE jobs SET state = 'queued', attempt = attempt - :refund, "
    "available_at = now() + make_interval(secs => :delay), lease_version = lease_version + 1, "
    "next_seq = next_seq + 1, updated_at = now(), "
    "leased_by = NULL, lease_expires_at = NULL, heartbeat_at = NULL "
    "WHERE id = :job_id AND owner_id = :owner_id AND lease_version = :v AND state = 'running' "
    "RETURNING next_seq, available_at"
)
_WAIT = text(
    "UPDATE jobs SET state = 'waiting_for_approval', approval_revision_id = :revision_id, "
    "approval_hash = :approval_hash, expires_at = now() + make_interval(hours => :ttl_hours), "
    "next_seq = next_seq + 1, updated_at = now(), "
    "leased_by = NULL, lease_expires_at = NULL, heartbeat_at = NULL "
    "WHERE id = :job_id AND owner_id = :owner_id AND lease_version = :v AND state = 'running' "
    "AND approved_at IS NULL "  # a job waits for approval at most once
    "RETURNING next_seq, expires_at"
)
_ATTEMPTS = text(
    "SELECT attempt, max_attempts, approved_at FROM jobs "
    "WHERE id = :job_id AND owner_id = :owner_id AND lease_version = :v AND state = 'running'"
)
_SET_STAGE = text(
    "UPDATE jobs SET stage = :stage, updated_at = now() "
    "WHERE id = :job_id AND owner_id = :owner_id AND lease_version = :v AND state = 'running'"
)
# Busy requeues in the current phase: since the job's ``approved`` event, else since creation.
_BUSY_COUNT = text(
    "SELECT count(*) FROM job_events e WHERE e.owner_id = :owner_id AND e.job_id = :job_id "
    "AND e.kind = 'retry_scheduled' AND e.payload ->> 'category' = 'operation_busy' "
    "AND e.seq > coalesce((SELECT max(a.seq) FROM job_events a WHERE a.owner_id = :owner_id "
    "AND a.job_id = :job_id AND a.kind = 'approved'), 0)"
)
# Retries of one category in the current phase (as ``_BUSY_COUNT``), for ``RETRIED_ONCE``.
_RETRIES_OF = text(
    "SELECT count(*) FROM job_events e WHERE e.owner_id = :owner_id AND e.job_id = :job_id "
    "AND e.kind = 'retry_scheduled' AND e.payload ->> 'category' = :category "
    "AND e.seq > coalesce((SELECT max(a.seq) FROM job_events a WHERE a.owner_id = :owner_id "
    "AND a.job_id = :job_id AND a.kind = 'approved'), 0)"
)
_NOTIFY_QUEUED = text("SELECT pg_notify('jobs_queued', '')")


class WaitedBefore(Exception):
    """The job was approved already: it waits for approval at most once (ADR 0005 section 2)."""


@dataclass(frozen=True, slots=True)
class Attempts:
    attempt: int
    max_attempts: int
    approved: bool


async def attempts(fs: FencedSession) -> Attempts:
    row = (await fs.db.execute(_ATTEMPTS, fs.lease.params())).first()
    if row is None:
        raise LeaseLost
    return Attempts(row.attempt, row.max_attempts, row.approved_at is not None)


async def _terminal(
    fs: FencedSession, state: str, category: str | None, kind: str, payload: dict[str, Any]
) -> str:
    seq = (
        await fs.db.execute(_TERMINAL, {**fs.lease.params(), "state": state, "category": category})
    ).scalar_one_or_none()
    if seq is None:
        raise LeaseLost
    await insert_event(fs.db, fs.lease, seq, kind, payload)
    return state


async def set_stage(fs: FencedSession, stage: str) -> None:
    result = await fs.db.execute(_SET_STAGE, {**fs.lease.params(), "stage": stage})
    if result.rowcount != 1:  # pyright: ignore[reportAttributeAccessIssue]
        raise LeaseLost
    await fs.event("stage_started", {"stage": stage})


async def complete(fs: FencedSession, revision_id: UUID | None) -> str:
    payload = {} if revision_id is None else {"revision_id": revision_id}
    return await _terminal(fs, "completed", None, "completed", payload)


async def fail(fs: FencedSession, category: str) -> str:
    return await _terminal(fs, "failed", category, "failed", {"category": category})


async def cancel(fs: FencedSession) -> str:
    return await _terminal(fs, "cancelled", None, "cancelled", {})


async def wait_for_approval(
    fs: FencedSession, revision_id: UUID, approval_hash: bytes, ttl_hours: int
) -> datetime:
    row = (
        await fs.db.execute(
            _WAIT,
            {
                **fs.lease.params(),
                "revision_id": revision_id,
                "approval_hash": approval_hash,
                "ttl_hours": ttl_hours,
            },
        )
    ).first()
    if row is None:
        if not (await attempts(fs)).approved:  # raises LeaseLost when the lease is gone
            raise LeaseLost
        raise WaitedBefore
    await insert_event(
        fs.db,
        fs.lease,
        row.next_seq,
        "waiting_for_approval",
        {"revision_id": revision_id, "expires_at": row.expires_at.isoformat()},
    )
    return row.expires_at


async def _requeue(
    fs: FencedSession, *, refund: bool, delay: float, kind: str, category: str | None
) -> str:
    row = (
        await fs.db.execute(
            _REQUEUE, {**fs.lease.params(), "refund": 1 if refund else 0, "delay": delay}
        )
    ).first()
    if row is None:
        raise LeaseLost
    payload: dict[str, Any] = {}
    if category is not None:
        payload = {"category": category, "available_at": row.available_at.isoformat()}
    await insert_event(fs.db, fs.lease, row.next_seq, kind, payload)
    if delay <= 0:
        await fs.db.execute(_NOTIFY_QUEUED)
    return "queued"


async def retry(fs: FencedSession, category: str, delay: float) -> str:
    """A retryable failure: a new attempt after ``delay`` seconds while attempts remain, else
    ``failed`` with the category; ``cancelled`` if a cancel was requested. A category of
    ``RETRIED_ONCE`` gets one retry per phase."""
    if fs.cancel_requested:
        return await cancel(fs)
    counts = await attempts(fs)
    if category in RETRIED_ONCE:
        done = await fs.db.execute(_RETRIES_OF, {**fs.lease.params(), "category": category})
        if done.scalar_one() >= 1:
            return await fail(fs, category)
    elif category not in RETRYABLE and category != "operation_busy":
        return await fail(fs, category)
    if counts.attempt >= counts.max_attempts:
        return await fail(fs, category)
    return await _requeue(fs, refund=False, delay=delay, kind="retry_scheduled", category=category)


async def hand_back(fs: FencedSession) -> str:
    """Shutdown (ADR 0005 section 3): back to ``queued`` at once, the attempt not counted."""
    if fs.cancel_requested:
        return await cancel(fs)
    return await _requeue(fs, refund=True, delay=0, kind="requeued", category=None)


async def busy_requeues(fs: FencedSession) -> int:
    return (await fs.db.execute(_BUSY_COUNT, fs.lease.params())).scalar_one()


async def operation_busy(fs: FencedSession, delay: float, limit: int) -> str:
    """Another running job holds the same external operation (ADR 0005 section 7): requeued with
    backoff, the attempt not counted, at most ``limit`` times per phase; after that each busy
    requeue counts an attempt (so a holder that keeps the operation cannot requeue this job
    forever; acceptance criterion added to #86)."""
    if fs.cancel_requested:
        return await cancel(fs)
    if await busy_requeues(fs) >= limit:
        return await retry(fs, "operation_busy", delay)
    return await _requeue(
        fs, refund=True, delay=delay, kind="retry_scheduled", category="operation_busy"
    )
