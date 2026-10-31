"""The jobs API's transactions (ADR 0005 section 2): one ``user_session`` per request, as the
signed-in user, around the transitions of ``classbeat.jobs.user``.

This is the web side of ``classbeat.jobs`` and never runs in the worker; it may open a
``user_session`` (``tests/session_scan.py``) because these are the user's own writes, not a
worker's, so the fence does not apply. Every read and write filters on ``owner_id =
ctx.user_id`` under RLS: another user's job id is not found, exactly like an unknown one.
"""

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from uuid import UUID

from pydantic import ValidationError
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from classbeat import connections
from classbeat.context import UserContext, require_context
from classbeat.db import user_session
from classbeat.jobs import user
from classbeat.jobs.credits import CreditLedger, QuotaRefused
from classbeat.jobs.inputs import JobInput
from classbeat.jobs.kinds import Approving, KindRegistry

Factory = async_sessionmaker[AsyncSession]

OPEN_PER_CLASS = "jobs_one_open_per_class"
KEY_UNIQUE = "jobs_owner_id_idempotency_key_key"
INPUT_SIZE = "jobs_input"

_VIEW = text(
    "SELECT id, kind, state, stage, class_id, cancel_requested_at IS NOT NULL AS "
    "cancel_requested, approval_revision_id, expires_at, approved_at, error_category, "
    "created_at, updated_at, finished_at, available_at, spotify_failure, spotify_retry_after "
    "FROM jobs WHERE id = :job_id AND owner_id = :owner_id"
)


@dataclass(frozen=True, slots=True)
class JobView:
    """What the owner sees of a job: no input, key, hash, lease or worker name."""

    id: UUID
    kind: str
    state: str
    stage: str | None
    class_id: UUID
    cancel_requested: bool
    approval_revision_id: UUID | None
    expires_at: datetime | None
    approved_at: datetime | None
    error_category: str | None
    created_at: datetime
    updated_at: datetime
    finished_at: datetime | None
    # For the page's sentence only (ADR 0009 section 8), never shown as such.
    available_at: datetime
    spotify_failure: str | None
    spotify_retry_after: int | None


async def _view(db: AsyncSession, ctx: UserContext, job_id: UUID) -> JobView | None:
    row = (await db.execute(_VIEW, {"job_id": job_id, "owner_id": ctx.user_id})).first()
    return None if row is None else JobView(*row)


def _constraint(error: IntegrityError) -> str | None:
    return getattr(getattr(error.orig, "diag", None), "constraint_name", None)


@dataclass(frozen=True, slots=True)
class CreateResult:
    created: bool
    job: JobView | None
    reason: str | None = None  # see ``user.Created``, plus quota and too_large
    # With admit's reason, busy (``limit``) and quota (``QuotaRefused.detail``).
    counts: Mapping[str, int | str] = field(default_factory=dict[str, int | str])


async def create(
    factory: Factory,
    ctx: UserContext,
    new: user.NewJob,
    *,
    limit: int | user.JobLimitPolicy,
    ledger: CreditLedger,
) -> CreateResult:
    require_context(ctx)
    for retry in (False, True):
        try:
            async with user_session(factory, ctx) as db:
                done = await user.create(db, ctx, new, limit=limit, ledger=ledger)
                if done.job_id is None:
                    return CreateResult(False, None, done.reason, done.counts)
                return CreateResult(done.created, await _view(db, ctx, done.job_id))
        except QuotaRefused as refused:  # the ledger's, or a revise kind's cost cap
            return CreateResult(False, None, "quota", refused.detail())
        except IntegrityError as error:
            # The owner lock serializes creations, so these are backstops, not the normal path.
            name = _constraint(error)
            if name == OPEN_PER_CLASS:
                return CreateResult(False, None, "class_busy")
            if name == INPUT_SIZE:
                return CreateResult(False, None, "too_large")
            if name != KEY_UNIQUE or retry:
                raise
    raise AssertionError("unreachable")  # pragma: no cover


async def get(factory: Factory, ctx: UserContext, job_id: UUID) -> JobView | None:
    async with user_session(factory, ctx) as db:
        return await _view(db, ctx, job_id)


async def cancel(factory: Factory, ctx: UserContext, job_id: UUID) -> JobView | None:
    async with user_session(factory, ctx) as db:
        if await user.cancel(db, ctx, job_id) is None:
            return None
        return await _view(db, ctx, job_id)


@dataclass(frozen=True, slots=True)
class ApproveResult:
    accepted: bool
    job: JobView | None
    reason: str | None = None
    counts: Mapping[str, int | str] = field(default_factory=dict[str, int | str])


async def approve(
    factory: Factory,
    ctx: UserContext,
    job_id: UUID,
    revision_id: UUID,
    content_hash: bytes,
    *,
    kinds: KindRegistry,
    limit: int | user.JobLimitPolicy,
    ledger: CreditLedger,
) -> ApproveResult:
    """Approve with every check of ADR 0005 section 2, in one transaction: the owner lock, the
    job row lock and its checks (``user.approve``), then ``admit`` below: the connections of the
    kind's ``CONNECTIONS``, the kind's ``approve_check`` (ADR 0008 section 5), the per-user limit
    and the credit seam, in that order. ``busy`` carries the limit; a ledger's ``QuotaRefused``
    (the cost cap) answers ``quota`` with its numbers and rolls the transaction back."""
    counts: dict[str, int | str] = {}

    async def admit(db: AsyncSession, waiting: user.Waiting) -> str | None:
        kind = kinds.get(waiting.kind)
        if kind is None or not issubclass(kind.input_model, JobInput):
            return "invalid_input"  # fail closed: the needed connections are unknown
        try:
            parsed = kind.parse_input(waiting.input)
        except ValidationError:
            return "invalid_input"
        assert isinstance(parsed, JobInput)  # noqa: S101 - parse_input returns input_model
        for provider, connection_id in parsed.connection_ids().items():
            if ctx.connection(provider) != connection_id:
                return "not_connected"
            info = await connections.find_in(db, ctx, connection_id)
            if info is None or info.status != "connected":
                return "not_connected"
        if kind.approve_check is not None:  # the kind's own refusal (ADR 0008 section 5)
            approving = Approving(
                waiting.job_id, waiting.class_id, waiting.revision_id, waiting.content_hash, parsed
            )
            reason = await kind.approve_check(db, ctx, approving)
            if reason is not None:
                return reason
        allowed = await user.job_limit(db, ctx, limit)
        if await user.active_jobs(db, ctx) >= allowed:  # the owner lock is held (user.approve)
            counts["limit"] = allowed
            return "busy"
        if waiting.credit_state == "reserved" and not await ledger.allows(db, ctx, waiting.job_id):
            return "quota"
        return None

    try:
        async with user_session(factory, ctx) as db:
            done = await user.approve(db, ctx, job_id, revision_id, content_hash, admit=admit)
            if done.state is None:
                return ApproveResult(False, None, done.reason)
            return ApproveResult(
                done.accepted, await _view(db, ctx, job_id), done.reason, dict(counts)
            )
    except QuotaRefused as refused:
        return ApproveResult(False, None, "quota", refused.detail())
