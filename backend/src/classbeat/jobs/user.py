"""The user's transitions on their own jobs (ADR 0005 section 2): cancel and approve.

Both take the caller's ``user_session`` (``db``) and its ``UserContext``, lock the job row
(``SELECT ... FOR UPDATE``, RLS as the owner, plus ``owner_id = ctx.user_id``; another user's id
is not found, like an unknown one) and are idempotent by their target state, writing no second
event. User events carry ``lease_version`` 0.

``approve`` checks what the job row can tell: the wait, its expiry, the revision (the newest
``song_list`` of the class) and the content hash. The connection, per-user-limit and credit
checks of ADR 0005 section 2 sit in front of it in the jobs API (J3). Accepted, it starts a new
**phase**: ``attempt = 0``, ``approved_at = now()``, state ``queued``, available at once, and it
wakes the workers (``jobs_queued``).
"""

import json
import uuid
from dataclasses import dataclass
from typing import Final
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from classbeat.context import UserContext, require_context

USER_LEASE_VERSION: Final = 0

_LOCK = text(
    "SELECT state, class_id, cancel_requested_at, approval_revision_id, approval_hash, "
    "approved_at, expires_at IS NOT NULL AND expires_at > now() AS live "
    "FROM jobs WHERE id = :job_id AND owner_id = :owner_id FOR UPDATE"
)
_CANCEL_NOW = text(
    "UPDATE jobs SET state = 'cancelled', cancel_requested_at = coalesce(cancel_requested_at, "
    "now()), finished_at = now(), next_seq = next_seq + 1, updated_at = now() "
    "WHERE id = :job_id AND owner_id = :owner_id AND state IN ('queued', 'waiting_for_approval') "
    "RETURNING next_seq"
)
_REQUEST_CANCEL = text(
    "UPDATE jobs SET cancel_requested_at = now(), next_seq = next_seq + 1, updated_at = now() "
    "WHERE id = :job_id AND owner_id = :owner_id AND state = 'running' "
    "AND cancel_requested_at IS NULL RETURNING next_seq"
)
_NEWEST_LIST = text(
    "SELECT id FROM class_revisions WHERE owner_id = :owner_id AND class_id = :class_id "
    "AND kind = 'song_list' ORDER BY number DESC LIMIT 1"
)
_APPROVE = text(
    "UPDATE jobs SET state = 'queued', attempt = 0, approved_at = now(), available_at = now(), "
    "next_seq = next_seq + 1, updated_at = now() "
    "WHERE id = :job_id AND owner_id = :owner_id AND state = 'waiting_for_approval' "
    "RETURNING next_seq"
)
_EVENT = text(
    "INSERT INTO job_events (id, owner_id, job_id, seq, kind, payload, lease_version) "
    "VALUES (:id, :owner_id, :job_id, :seq, :kind, CAST(:payload AS jsonb), :v)"
)
_NOTIFY_QUEUED = text("SELECT pg_notify('jobs_queued', '')")


async def _event(db: AsyncSession, ctx: UserContext, job_id: UUID, seq: int, kind: str) -> None:
    await db.execute(
        _EVENT,
        {
            "id": uuid.uuid4(),
            "owner_id": ctx.user_id,
            "job_id": job_id,
            "seq": seq,
            "kind": kind,
            "payload": json.dumps({}),
            "v": USER_LEASE_VERSION,
        },
    )


async def cancel(db: AsyncSession, ctx: UserContext, job_id: UUID) -> str | None:
    """Cancel: ``queued`` and ``waiting_for_approval`` end ``cancelled`` at once; a ``running``
    job gets ``cancel_requested_at`` and stops at its next checkpoint. Returns the job's state
    afterwards, or ``None`` if the user has no such job."""
    require_context(ctx)
    params = {"job_id": job_id, "owner_id": ctx.user_id}
    row = (await db.execute(_LOCK, params)).first()
    if row is None:
        return None
    if row.state in ("queued", "waiting_for_approval"):
        seq = (await db.execute(_CANCEL_NOW, params)).scalar_one()
        await _event(db, ctx, job_id, seq, "cancelled")
        return "cancelled"
    if row.state == "running" and row.cancel_requested_at is None:
        seq = (await db.execute(_REQUEST_CANCEL, params)).scalar_one()
        await _event(db, ctx, job_id, seq, "cancel_requested")
    return row.state


@dataclass(frozen=True, slots=True)
class Approval:
    accepted: bool
    state: str | None
    reason: str | None = None  # not_found, not_waiting, expired, superseded, hash_mismatch


async def approve(
    db: AsyncSession, ctx: UserContext, job_id: UUID, revision_id: UUID, content_hash: bytes
) -> Approval:
    require_context(ctx)
    params = {"job_id": job_id, "owner_id": ctx.user_id}
    row = (await db.execute(_LOCK, params)).first()
    if row is None:
        return Approval(False, None, "not_found")
    same = row.approval_revision_id == revision_id and (
        row.approval_hash is not None and bytes(row.approval_hash) == content_hash
    )
    if row.state != "waiting_for_approval":
        if row.approved_at is not None and same:
            return Approval(True, row.state)  # a repeated approval: no second event
        return Approval(False, row.state, "not_waiting")
    if not row.live:
        return Approval(False, row.state, "expired")
    newest = (
        await db.execute(_NEWEST_LIST, {"owner_id": ctx.user_id, "class_id": row.class_id})
    ).scalar_one_or_none()
    if row.approval_revision_id != revision_id or newest != revision_id:
        return Approval(False, row.state, "superseded")
    if not same:
        return Approval(False, row.state, "hash_mismatch")
    seq = (await db.execute(_APPROVE, params)).scalar_one()
    await _event(db, ctx, job_id, seq, "approved")
    await db.execute(_NOTIFY_QUEUED)
    return Approval(True, "queued")
