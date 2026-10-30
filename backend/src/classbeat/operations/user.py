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

**Pool rows** (a Spotify create or replace without a class, ADR 0008 section 2) are never shown
or acted on: listed nowhere and not found by id (404), so the user is never asked to resolve a
pool write; the next pick supersedes them (``classbeat.playlists.pool``).

Neither applies to a ``succeeded`` row (``final``: it is final) or a ``pending`` one
(``not_uncertain``: a call is in flight or its holder has not been reaped yet). Both are
idempotent by their target: Do it anyway on a ``failed`` row answers with that row.

ADR 0007 section 8: both first mark the user's orphaned ``pending`` class writes ``uncertain``
(``classbeat.operations.orphans.resolve``, same transaction as the read), and both are refused
with ``call_may_be_running`` while a stale multi-request call may still be sending requests
(before ``actionable_at``). Check again passes the stored ``context`` to the reconciler and to
``on_succeeded``. The view shows the call timestamps and ``actionable_at``, never the key,
external id, context or result.
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
from classbeat.operations import orphans
from classbeat.operations.kinds import (
    OPERATION_KINDS,
    SPOTIFY_WRITE_KINDS,
    OperationKinds,
    ReconcileInput,
)
from classbeat.operations.protocol import RESOLVE, operation_of, reconcile, succeeded
from classbeat.operations.requests import ACTIONABLE_AT_SQL, NOT_ACTIONABLE_SQL

Factory = async_sessionmaker[AsyncSession]
LIST_LIMIT: Final = 50
# ADR 0008 section 2: the user is never asked to resolve a pool write. A Spotify row without a
# class (the pool's create and replace) is not shown and not acted on: not found, like another
# user's id, so no user action ever re-sends a pool create under the same id and ``ref``.
_SPOTIFY_KINDS_SQL: Final = ", ".join(f"'{name}'" for name in sorted(SPOTIFY_WRITE_KINDS))
_OFFERED: Final = f"NOT (kind IN ({_SPOTIFY_KINDS_SQL}) AND class_id IS NULL)"

_FIELDS = (
    "id, kind, idempotency_key, state, external_id, result, job_id, resolved_by, created_at, "
    "updated_at, resolved_at, class_id, request_context, pending_at, call_ended_at, "
    f"{ACTIONABLE_AT_SQL} AS actionable_at, {NOT_ACTIONABLE_SQL} AS waiting"
)
_ONE = text(
    f"SELECT {_FIELDS} FROM external_operations "  # noqa: S608 - constant column list
    f"WHERE id = :id AND owner_id = :owner_id AND {_OFFERED}"
)
_LOCK_ONE = text(
    f"SELECT {_FIELDS} FROM external_operations "  # noqa: S608 - constant column list
    f"WHERE id = :id AND owner_id = :owner_id AND {_OFFERED} FOR UPDATE"
)
_UNCERTAIN = text(
    f"SELECT {_FIELDS} FROM external_operations "  # noqa: S608 - constant column list
    f"WHERE owner_id = :owner_id AND state = 'uncertain' AND {_OFFERED} "
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
    class_id: UUID | None = None
    pending_at: datetime | None = None
    call_ended_at: datetime | None = None
    # Set while a stale multi-request call may still be running (ADR 0007 section 8): Check
    # again and Do it anyway are refused before it. None: actionable now.
    actionable_at: datetime | None = None


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
        class_id=row.class_id,
        pending_at=row.pending_at,
        call_ended_at=row.call_ended_at,
        actionable_at=row.actionable_at if row.state in ("pending", "uncertain") else None,
    )


@dataclass(frozen=True, slots=True)
class Resolution:
    """``operation`` is the row afterwards; ``reason`` is set when the action was refused:
    ``not_found``, ``final`` (succeeded), ``not_uncertain`` (pending, or failed for Check
    again) or ``call_may_be_running`` (before ``actionable_at``)."""

    operation: OperationView | None
    reason: str | None = None


async def get(factory: Factory, ctx: UserContext, operation_id: UUID) -> OperationView | None:
    require_context(ctx)
    async with user_session(factory, ctx) as db:
        await orphans.resolve(db, ctx)
        row = (await db.execute(_ONE, {"id": operation_id, "owner_id": ctx.user_id})).first()
    return None if row is None else _view(row)


async def uncertain(
    factory: Factory, ctx: UserContext, job_id: UUID | None = None
) -> list[OperationView]:
    """The user's operations waiting for their decision, newest first (of one job if given)."""
    require_context(ctx)
    async with user_session(factory, ctx) as db:
        await orphans.resolve(db, ctx)
        rows = await db.execute(
            _UNCERTAIN,
            {
                "owner_id": ctx.user_id,
                "job_id": None if job_id is None else str(job_id),
                "limit": LIST_LIMIT,
            },
        )
        return [_view(row) for row in rows]


def _refusal(row: Any, *, failed_is_done: bool) -> str | None:
    if row.state == "succeeded":
        return "final"
    if row.state == "uncertain":
        return "call_may_be_running" if row.waiting else None
    if row.state == "failed" and failed_is_done:
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
        await orphans.resolve(db, ctx)
        row = (await db.execute(_ONE, ids)).first()
    if row is None:
        return Resolution(None, "not_found")
    refused = _refusal(row, failed_is_done=False)
    if refused is not None:
        return Resolution(_view(row), refused)
    kind = kinds.require(row.kind)
    read = operation_of(row)
    found = await reconcile(kind, ReconcileInput(factory, keyring, ctx, read))
    if found.outcome == "inconclusive":
        return Resolution(_view(row))
    async with user_session(factory, ctx) as db:
        moved = await db.execute(
            RESOLVE,
            {
                **ids,
                "state": found.outcome,
                "external_id": found.external_id,
                "by": "user",
                "pending_at": read.pending_at,
            },
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
        await orphans.resolve(db, ctx)
        row = (await db.execute(_LOCK_ONE, ids)).first()
        if row is None:
            return Resolution(None, "not_found")
        refused = _refusal(row, failed_is_done=True)
        if refused is not None or row.state == "failed":
            return Resolution(_view(row), refused)
        moved = await db.execute(
            RESOLVE,
            {
                **ids,
                "state": "failed",
                "external_id": None,
                "by": "user",
                "pending_at": row.pending_at,
            },
        )
        if moved.first() is None:  # the row is locked: only the time check can refuse it
            return Resolution(_view(row), "call_may_be_running")
        now = (await db.execute(_ONE, ids)).one()
    return Resolution(_view(now))
