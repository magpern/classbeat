"""Orphaned ``pending`` class writes and the unresolved-write rule (ADR 0007 sections 6 and 8).

Operation keys of the class writes are per job (``<job_id>:save``), so the only job that can meet
a ``pending`` ``mowl_save_workout`` / ``mowl_update_workout`` row is the same job's next attempt.
When ``queue_reap`` ends the job instead (``failed`` / ``lease_lost``, or ``cancelled`` after a
cancel request), or the job expires, completes or is deleted, nobody else would ever resolve the
row (#117 item 7). The queue functions do not change; instead:

- ``resolve(db, ctx)`` marks every such row of the user ``uncertain``, conditional on ``state =
  'pending'`` and on the job being terminal or gone (``job_id`` NULL; a row of an open job is its
  holder's), with the job's error category when it has one. It writes in the caller's
  transaction, under the user's own row security: in the web process a ``user_session``
  (``resolve_now`` opens one; this
  module is on the session guard's allowlist for that), on the worker path **the job's fenced
  session** (``fs.db``), never a session of its own. It runs before the class and operation
  views, Save and Update admit, class delete, Check again and Do it anyway, and in a worker's
  ``prepare`` for the owner. The row has no ``call_ended_at``, so ``actionable_at`` still applies
  to it (``classbeat.operations.protocol``). A late outcome write of the fenced-out holder fails
  on its lease (and on ``state = 'pending'``), so it can't overwrite the marking.
- ``write_unresolved(db, ctx, class_id, job_id)`` is the rule shared by Save, Update and their
  ``prepare``: a ``pending`` row of another job, or any ``uncertain`` row, of the class blocks a
  new write (409 ``write_unresolved``) until the user resolves it with Check again or Do it
  anyway. It runs ``resolve`` first in the same transaction.

Only ids and counts are logged, never a row's context or result.
"""

from typing import Final
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from classbeat.context import UserContext, require_context
from classbeat.db import user_session

Factory = async_sessionmaker[AsyncSession]

# migration 0004's terminal job states
TERMINAL_JOB_STATES: Final = ("completed", "failed", "cancelled", "expired")

# A deleted job leaves ``job_id`` NULL (migration 0004: ``ON DELETE SET NULL (job_id)``), so "the
# job is gone" is ``o.job_id IS NULL``; a row can't name a job that does not exist.
_RESOLVE = text(
    "UPDATE external_operations AS o SET state = 'uncertain', "
    "error_category = coalesce((SELECT j.error_category FROM jobs j "
    "WHERE j.owner_id = o.owner_id AND j.id = o.job_id), o.error_category), updated_at = now() "
    "WHERE o.owner_id = :owner_id AND o.state = 'pending' "
    "AND o.kind IN ('mowl_save_workout', 'mowl_update_workout') "
    "AND (o.job_id IS NULL "
    "OR EXISTS (SELECT 1 FROM jobs j WHERE j.owner_id = o.owner_id AND j.id = o.job_id "
    "AND j.state IN ('completed', 'failed', 'cancelled', 'expired'))) "
    "RETURNING o.id"
)
_UNRESOLVED = text(
    "SELECT 1 FROM external_operations "
    "WHERE owner_id = :owner_id AND class_id = :class_id "
    "AND kind IN ('mowl_save_workout', 'mowl_update_workout') "
    "AND (state = 'uncertain' "
    "OR (state = 'pending' AND job_id IS DISTINCT FROM CAST(:job_id AS uuid))) LIMIT 1"
)


async def resolve(db: AsyncSession, ctx: UserContext) -> int:
    """Mark the user's orphaned ``pending`` class writes ``uncertain`` in the caller's
    transaction (a ``user_session`` of ``ctx``, or the job's fenced session); returns how many."""
    require_context(ctx)
    marked = await db.execute(_RESOLVE, {"owner_id": ctx.user_id})
    return len(marked.all())


async def resolve_now(factory: Factory, ctx: UserContext) -> int:
    """``resolve`` in a transaction of its own, for a web route that reads operations next."""
    require_context(ctx)
    async with user_session(factory, ctx) as db:
        return await resolve(db, ctx)


async def write_unresolved(
    db: AsyncSession, ctx: UserContext, class_id: UUID, job_id: UUID | None = None
) -> bool:
    """True when a new Save or Update of ``class_id`` must be refused (``write_unresolved``):
    a ``pending`` row of a job other than ``job_id`` (None at admit: any job), or an
    ``uncertain`` row, of a class write of that class. Runs ``resolve`` first."""
    await resolve(db, ctx)
    found = await db.execute(
        _UNRESOLVED,
        {
            "owner_id": ctx.user_id,
            "class_id": class_id,
            "job_id": None if job_id is None else str(job_id),
        },
    )
    return found.first() is not None
