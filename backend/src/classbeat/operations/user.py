"""The user's actions on their own external operations (ADR 0005 section 7): list, **Check
again** and **Do it anyway**.

This module may open a ``user_session`` itself (``tests/session_scan.py``): these are user
actions in the web process, on the caller's own rows (RLS as ``ctx.user_id``, plus ``owner_id =
ctx.user_id``; another user's id is not found, like an unknown one), never worker writes, which
go through the fence (``classbeat.operations.protocol``).

- **Check again** reconciles once more through the kind's ``Reconciler`` (outside any
  transaction) and records what it found conditional on ``state = 'uncertain'``: ``succeeded``
  (with the kind's ``on_succeeded`` writes, the attach's link, in the same transaction) or
  ``failed``; inconclusive leaves the row ``uncertain``.
- **Do it anyway** is the user's explicit decision that the write did not happen: ``failed``,
  ``resolved_by = 'user'``, conditional on ``state = 'uncertain'``. The next job on the same
  target then takes the ``failed`` row over and makes the call again.

Neither applies to a ``succeeded`` row (``final``: it is final) or a ``pending`` one
(``not_uncertain``: a call is in flight or its holder has not been reaped yet). Both are
idempotent by their target: Do it anyway on a ``failed`` row answers with that row.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Final
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from classbeat.context import UserContext, require_context
from classbeat.crypto.keks import KeyRing
from classbeat.db import user_session
from classbeat.operations.kinds import OPERATION_KINDS, OperationKinds, ReconcileInput
from classbeat.operations.protocol import RESOLVE, operation_of, reconcile, succeeded

Factory = async_sessionmaker[AsyncSession]
LIST_LIMIT: Final = 50

_FIELDS = (
    "id, kind, idempotency_key, state, external_id, result, job_id, resolved_by, created_at, "
    "updated_at, resolved_at"
)
_ONE = text(
    f"SELECT {_FIELDS} FROM external_operations "  # noqa: S608 - constant column list
    "WHERE id = :id AND owner_id = :owner_id"
)
_LOCK_ONE = text(
    f"SELECT {_FIELDS} FROM external_operations "  # noqa: S608 - constant column list
    "WHERE id = :id AND owner_id = :owner_id FOR UPDATE"
)
_UNCERTAIN = text(
    f"SELECT {_FIELDS} FROM external_operations "  # noqa: S608 - constant column list
    "WHERE owner_id = :owner_id AND state = 'uncertain' "
    "AND (CAST(:job_id AS uuid) IS NULL OR job_id = CAST(:job_id AS uuid)) "
    "ORDER BY created_at DESC LIMIT :limit"
)


@dataclass(frozen=True, slots=True)
class OperationView:
    """What the user sees of an operation: never its key, external id or result (provider ids
    stay in the backend, as in events, ADR 0005 section 6)."""

    id: UUID
    kind: str
    state: str
    job_id: UUID | None
    resolved_by: str | None
    created_at: datetime
    updated_at: datetime
    resolved_at: datetime | None


def _view(row: Any) -> OperationView:
    return OperationView(
        id=row.id,
        kind=row.kind,
        state=row.state,
        job_id=row.job_id,
        resolved_by=row.resolved_by,
        created_at=row.created_at,
        updated_at=row.updated_at,
        resolved_at=row.resolved_at,
    )


@dataclass(frozen=True, slots=True)
class Resolution:
    """``operation`` is the row afterwards; ``reason`` is set when the action was refused:
    ``not_found``, ``final`` (succeeded) or ``not_uncertain`` (pending, or failed for Check
    again)."""

    operation: OperationView | None
    reason: str | None = None


async def get(factory: Factory, ctx: UserContext, operation_id: UUID) -> OperationView | None:
    require_context(ctx)
    async with user_session(factory, ctx) as db:
        row = (await db.execute(_ONE, {"id": operation_id, "owner_id": ctx.user_id})).first()
    return None if row is None else _view(row)


async def uncertain(
    factory: Factory, ctx: UserContext, job_id: UUID | None = None
) -> list[OperationView]:
    """The user's operations waiting for their decision, newest first (of one job if given)."""
    require_context(ctx)
    async with user_session(factory, ctx) as db:
        rows = await db.execute(
            _UNCERTAIN,
            {
                "owner_id": ctx.user_id,
                "job_id": None if job_id is None else str(job_id),
                "limit": LIST_LIMIT,
            },
        )
        return [_view(row) for row in rows]


def _refusal(state: str, *, failed_is_done: bool) -> str | None:
    if state == "succeeded":
        return "final"
    if state == "uncertain" or (state == "failed" and failed_is_done):
        return None
    return "not_uncertain"


async def check_again(
    factory: Factory,
    keyring: KeyRing | None,
    ctx: UserContext,
    operation_id: UUID,
    *,
    kinds: OperationKinds = OPERATION_KINDS,
) -> Resolution:
    require_context(ctx)
    ids = {"id": operation_id, "owner_id": ctx.user_id}
    async with user_session(factory, ctx) as db:
        row = (await db.execute(_ONE, ids)).first()
    if row is None:
        return Resolution(None, "not_found")
    refused = _refusal(row.state, failed_is_done=False)
    if refused is not None:
        return Resolution(_view(row), refused)
    kind = kinds.require(row.kind)
    found = await reconcile(kind, ReconcileInput(factory, keyring, ctx, operation_of(row)))
    if found.outcome == "inconclusive":
        return Resolution(_view(row))
    async with user_session(factory, ctx) as db:
        moved = await db.execute(
            RESOLVE, {**ids, "state": found.outcome, "external_id": found.external_id, "by": "user"}
        )
        resolved_here = moved.first() is not None
        now = (await db.execute(_ONE, ids)).first()
        if now is None:  # a MOWL disconnect deleted it meanwhile
            return Resolution(None, "not_found")
        if resolved_here and found.outcome == "succeeded":
            await succeeded(db, kind, ctx.user_id, operation_of(now))
    return Resolution(_view(now))


async def do_it_anyway(factory: Factory, ctx: UserContext, operation_id: UUID) -> Resolution:
    require_context(ctx)
    ids = {"id": operation_id, "owner_id": ctx.user_id}
    async with user_session(factory, ctx) as db:
        row = (await db.execute(_LOCK_ONE, ids)).first()
        if row is None:
            return Resolution(None, "not_found")
        refused = _refusal(row.state, failed_is_done=True)
        if refused is not None or row.state == "failed":
            return Resolution(_view(row), refused)
        await db.execute(RESOLVE, {**ids, "state": "failed", "external_id": None, "by": "user"})
        now = (await db.execute(_ONE, ids)).one()
    return Resolution(_view(now))
