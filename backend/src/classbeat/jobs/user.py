"""The user's transitions on their own jobs (ADR 0005 section 2): create, cancel and approve.

All take the caller's ``user_session`` (``db``) and its ``UserContext``. Cancel and approve lock
the job row (``SELECT ... FOR UPDATE``, RLS as the owner, plus ``owner_id = ctx.user_id``;
another user's id is not found, like an unknown one) and are idempotent by their target state,
writing no second event. User events carry ``lease_version`` 0.

``create`` and ``approve`` first take the owner's ``pg_advisory_xact_lock`` (``lock_owner``), so
two creations, two approvals, or a creation and an approval of one user are serialized before
either counts the per-user limit (``CLASSBEAT_JOBS_PER_USER``: ``queued`` and ``running``).
The lock is always taken before any job row lock, and no worker path takes it, so it adds no
lock cycle.

``create`` runs the kind's ``admit`` (``NewJob.admit``, ADR 0007 section 3) last of its checks,
still under the owner lock and before anything is inserted, in a savepoint: a refusal rolls back
whatever ``admit`` wrote and creates no class and no job. A job of a kind that consumes no credit
is inserted with ``credit_state = 'none'`` and the ledger is not called.

``approve`` checks what the job row can tell: the wait, its expiry, the revision (the newest
``song_list`` of the class) and the content hash; then the caller's ``admit`` (the jobs API:
connections, the per-user limit and the credit seam, ADR 0005 section 2) in the same
transaction. Accepted, it starts a new **phase**: ``attempt = 0``, ``approved_at = now()``,
state ``queued``, available at once, and it wakes the workers (``jobs_queued``).
"""

import json
import uuid
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from typing import Any, Final, Protocol
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from classbeat.context import UserContext, require_context
from classbeat.jobs.credits import CreditLedger
from classbeat.jobs.events import notify_event, validated_payload
from classbeat.jobs.kinds import Refused

USER_LEASE_VERSION: Final = 0
# The owner lock's key namespace, so it never shares a key with another advisory lock.
OWNER_LOCK_NAMESPACE: Final = "classbeat.jobs.owner:"

_LOCK_OWNER = text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))")
_ACTIVE = text(
    "SELECT count(*) FROM jobs WHERE owner_id = :owner_id AND state IN ('queued', 'running')"
)
_BY_KEY = text(
    "SELECT id, request_hash FROM jobs WHERE owner_id = :owner_id AND idempotency_key = :key"
)
_CLASS = text("SELECT 1 FROM classes WHERE id = :class_id AND owner_id = :owner_id")
_CLASS_OPEN = text(
    "SELECT 1 FROM jobs WHERE owner_id = :owner_id AND class_id = :class_id "
    "AND state IN ('queued', 'running', 'waiting_for_approval')"
)
_NEW_CLASS = text("INSERT INTO classes (id, owner_id, title) VALUES (:id, :owner_id, :title)")
_NEW_JOB = text(
    "INSERT INTO jobs (id, owner_id, kind, state, class_id, input, idempotency_key, "
    "request_hash, credit_state, next_seq) VALUES (:id, :owner_id, :kind, 'queued', :class_id, "
    "CAST(:input AS jsonb), :key, :request_hash, :credit_state, 1)"
)
_LOCK = text(
    "SELECT state, kind, input, class_id, cancel_requested_at, approval_revision_id, "
    "approval_hash, approved_at, credit_state, "
    "expires_at IS NOT NULL AND expires_at > now() AS live "
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


async def lock_owner(db: AsyncSession, ctx: UserContext) -> None:
    """The owner's transaction-level advisory lock (ADR 0005 section 2)."""
    await db.execute(_LOCK_OWNER, {"key": f"{OWNER_LOCK_NAMESPACE}{ctx.user_id}"})


async def active_jobs(db: AsyncSession, ctx: UserContext) -> int:
    """The owner's ``queued`` and ``running`` jobs: what the per-user limit counts."""
    return (await db.execute(_ACTIVE, {"owner_id": ctx.user_id})).scalar_one()


async def _event(db: AsyncSession, ctx: UserContext, job_id: UUID, seq: int, kind: str) -> None:
    await db.execute(
        _EVENT,
        {
            "id": uuid.uuid4(),
            "owner_id": ctx.user_id,
            "job_id": job_id,
            "seq": seq,
            "kind": kind,
            "payload": json.dumps(validated_payload(kind, {})),
            "v": USER_LEASE_VERSION,
        },
    )
    await notify_event(db, job_id)


@dataclass(frozen=True, slots=True)
class Admission:
    """What the kind's ``admit`` accepted: the input to store (canonical JSON, with the
    server-resolved fields) and what runs once the job row exists."""

    input: str
    after_insert: Callable[[AsyncSession, UUID], Awaitable[None]] | None = None


class CreateAdmit(Protocol):
    """The kind's create-time check, bound to the parsed input by the jobs API."""

    def __call__(self, db: AsyncSession, ctx: UserContext, /) -> Awaitable[Admission | Refused]: ...


@dataclass(frozen=True, slots=True)
class NewJob:
    """A validated ``POST /api/jobs``: the stored ``input`` JSON, its class (an existing id or
    the title of a new class, exactly one), the connection id it uses per provider, the
    browser's idempotency key and the SHA-256 of the canonical validated body (as the browser
    sent it, before ``admit`` adds anything, so a retry matches). ``consumes_credit`` and
    ``admit`` are the kind's (``classbeat.jobs.kinds.JobKind``)."""

    kind: str
    input: str
    class_id: UUID | None
    title: str | None
    connection_ids: Mapping[str, UUID]
    idempotency_key: str
    request_hash: bytes
    consumes_credit: bool = True
    admit: CreateAdmit | None = None


@dataclass(frozen=True, slots=True)
class Created:
    job_id: UUID | None
    created: bool  # False: the same key and body again (the existing job), or refused
    # idempotency_mismatch, not_connected, class_not_found, class_busy, busy, or admit's reason
    reason: str | None = None
    counts: Mapping[str, int] = field(default_factory=dict[str, int])


async def create(
    db: AsyncSession, ctx: UserContext, new: NewJob, *, limit: int, ledger: CreditLedger
) -> Created:
    """``(new) -> queued`` in the caller's transaction, under the owner lock, in ADR 0005
    section 2's order. A refusal writes nothing; ``QuotaRefused`` from the ledger propagates
    and the caller's transaction rolls back."""
    require_context(ctx)
    owner = {"owner_id": ctx.user_id}
    await lock_owner(db, ctx)
    found = (await db.execute(_BY_KEY, {**owner, "key": new.idempotency_key})).first()
    if found is not None:
        if bytes(found.request_hash) == new.request_hash:
            return Created(found.id, created=False)
        return Created(None, created=False, reason="idempotency_mismatch")
    for provider, connection_id in new.connection_ids.items():
        if ctx.connection(provider) != connection_id:
            return Created(None, created=False, reason="not_connected")
    class_id = new.class_id
    if class_id is not None:
        if (await db.execute(_CLASS, {**owner, "class_id": class_id})).first() is None:
            return Created(None, created=False, reason="class_not_found")
        if (await db.execute(_CLASS_OPEN, {**owner, "class_id": class_id})).first() is not None:
            return Created(None, created=False, reason="class_busy")
    if await active_jobs(db, ctx) >= limit:
        return Created(None, created=False, reason="busy")
    stored = new.input
    after_insert = None
    if new.admit is not None:
        # An exception rolls back the whole transaction (the caller's ``user_session``).
        savepoint = await db.begin_nested()
        admitted = await new.admit(db, ctx)
        if isinstance(admitted, Refused):
            await savepoint.rollback()  # nothing admit wrote survives a refusal
            return Created(None, created=False, reason=admitted.reason, counts=admitted.counts)
        await savepoint.commit()
        stored, after_insert = admitted.input, admitted.after_insert
    if class_id is None:
        class_id = uuid.uuid4()
        await db.execute(_NEW_CLASS, {**owner, "id": class_id, "title": new.title})
    job_id = uuid.uuid4()
    await db.execute(
        _NEW_JOB,
        {
            **owner,
            "id": job_id,
            "kind": new.kind,
            "class_id": class_id,
            "input": stored,
            "key": new.idempotency_key,
            "request_hash": new.request_hash,
            "credit_state": "reserved" if new.consumes_credit else "none",
        },
    )
    if after_insert is not None:
        await after_insert(db, job_id)
    if new.consumes_credit:
        # After the insert, so a metered ledger (M5) can refer to the job; the same transaction.
        await ledger.reserve(db, ctx, job_id)
    await _event(db, ctx, job_id, 1, "queued")
    await db.execute(_NOTIFY_QUEUED)
    return Created(job_id, created=True)


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
class Waiting:
    """What ``admit`` sees of a job that passed the row checks of an approval."""

    job_id: UUID
    kind: str
    input: Mapping[str, Any]
    credit_state: str  # the job row's: 'reserved' when it consumes a credit, else 'none'


# The jobs API's checks at approval (connections, per-user limit, credit): a refusal reason, or
# None to accept. Runs in the approval's transaction, after the owner lock and the row lock.
Admit = Callable[[AsyncSession, Waiting], Awaitable[str | None]]


@dataclass(frozen=True, slots=True)
class Approval:
    accepted: bool
    state: str | None
    # not_found, not_waiting, expired, superseded, hash_mismatch, or the reason ``admit`` gave
    reason: str | None = None


async def approve(
    db: AsyncSession,
    ctx: UserContext,
    job_id: UUID,
    revision_id: UUID,
    content_hash: bytes,
    *,
    admit: Admit,
) -> Approval:
    """``admit`` is required (#115 item 7): it holds the connection, per-user limit and credit
    checks, and leaving it out would skip them."""
    require_context(ctx)
    params = {"job_id": job_id, "owner_id": ctx.user_id}
    await lock_owner(db, ctx)  # before anything is counted (ADR 0005 section 2, #87)
    row = (await db.execute(_LOCK, params)).first()
    if row is None:
        return Approval(False, None, "not_found")
    same = row.approval_revision_id == revision_id and (
        row.approval_hash is not None and bytes(row.approval_hash) == content_hash
    )
    if row.state != "waiting_for_approval":
        if row.approved_at is not None and same:
            return Approval(True, row.state)  # a repeated approval: no second event
        if row.state == "expired":  # the sweep got there first: same reason as a late approval
            return Approval(False, row.state, "expired")
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
    reason = await admit(db, Waiting(job_id, row.kind, row.input, row.credit_state))
    if reason is not None:
        return Approval(False, row.state, reason)
    seq = (await db.execute(_APPROVE, params)).scalar_one()
    await _event(db, ctx, job_id, seq, "approved")
    await db.execute(_NOTIFY_QUEUED)
    return Approval(True, "queued")
