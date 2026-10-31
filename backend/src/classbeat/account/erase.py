"""Deleting the account (ADR 0014 section 3).

1. **Confirmation**: the typed email must be the account's (normalized), checked before anything
   changes. The route has already required recent authentication and, with two-factor on, a
   step-up.
2. **Open jobs end** (the operator's delete of an account takes the same step, ``end_jobs_of``):
   every ``queued`` and ``waiting_for_approval`` job is cancelled at once, a
   ``running`` one is asked to stop (``classbeat.jobs.user.cancel``, the same transition as the
   user's Cancel) and given ``wait`` seconds to reach a checkpoint. The credit of every ended
   job is then settled exactly as the worker's settle step does (``classbeat.jobs.credits``): the
   reserved credit is released or committed before the ledger goes.
3. **The row goes**: under the owner's lock (so no job is created between the steps) the ``users``
   row is deleted and the foreign keys cascade through every table of the account (sessions,
   identities, connections' ciphertext, classes with their revisions, jobs and events, Spotify
   pool and playlist rows, usage, plans, credits, quota rows, security events). A job still
   running after ``wait`` fails its next fenced write and ends; nothing it writes survives.

Only the ``account_deleted`` event is kept, with no user id and no address (the 30-day purge
removes it). Anything in the user's own MOWL or Spotify account stays there.
"""

import asyncio
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from classbeat import auth
from classbeat.context import UserContext, require_context
from classbeat.db import system_session, user_session
from classbeat.emailnorm import normalize_email
from classbeat.jobs import credits, user
from classbeat.jobs.credits import CreditLedger
from classbeat.jobs.issuer import owner_context

Factory = async_sessionmaker[AsyncSession]

# How long a running job may take to reach a checkpoint before the account goes anyway.
RUNNING_WAIT_SECONDS = 15.0
POLL_SECONDS = 0.25

_OPEN = text(
    "SELECT id FROM jobs WHERE owner_id = :owner_id "
    "AND state IN ('queued', 'waiting_for_approval', 'running') ORDER BY created_at, id"
)
_RUNNING = text("SELECT count(*) FROM jobs WHERE owner_id = :owner_id AND state = 'running'")
_UNSETTLED = text(
    "SELECT id FROM jobs WHERE owner_id = :owner_id AND credit_state = 'reserved' "
    "AND state IN ('completed', 'failed', 'cancelled', 'expired') ORDER BY created_at, id"
)


@dataclass(frozen=True, slots=True)
class Ended:
    cancelled: int  # open jobs that were asked to stop
    still_running: int  # jobs that had not stopped when the wait ran out
    settled: int  # credits released or committed


async def confirmed(factory: Factory, ctx: UserContext, typed: str) -> bool:
    """Whether ``typed`` is the signed-in account's own email (normalized); ``False`` for an
    account that is gone."""
    async with system_session(factory) as db:
        account = await auth.get_account(db, ctx.user_id)
    if account is None:
        return False
    try:
        return normalize_email(typed) == account.email_normalized
    except ValueError:  # not an address at all
        return False


async def end_jobs(
    factory: Factory, ctx: UserContext, ledger: CreditLedger, *, wait: float = RUNNING_WAIT_SECONDS
) -> Ended:
    require_context(ctx)
    params = {"owner_id": ctx.user_id}
    async with user_session(factory, ctx) as db:
        await user.lock_owner(db, ctx)
        ids: list[UUID] = [row[0] for row in (await db.execute(_OPEN, params)).all()]
        for job_id in ids:
            await user.cancel(db, ctx, job_id)
    deadline = time.monotonic() + wait
    running = await _running(factory, ctx)
    while running and time.monotonic() < deadline:
        await asyncio.sleep(POLL_SECONDS)
        running = await _running(factory, ctx)
    async with user_session(factory, ctx) as db:
        unsettled: list[UUID] = [row[0] for row in (await db.execute(_UNSETTLED, params)).all()]
    settled = 0
    for job_id in unsettled:
        if await credits.settle_one(factory, ledger, ctx.user_id, job_id) is not None:
            settled += 1
    return Ended(len(ids), running, settled)


async def end_jobs_of(
    factory: Factory, user_id: UUID, ledger: CreditLedger, *, wait: float = RUNNING_WAIT_SECONDS
) -> Ended:
    """``end_jobs`` for the operator's delete of someone else's account: the same path, as the
    target's own context (what the worker's settle step uses)."""
    return await end_jobs(factory, owner_context(user_id), ledger, wait=wait)


def make_job_ender(factory: Factory, ledger: CreditLedger) -> Callable[[UUID], Awaitable[Ended]]:
    """``end_jobs_of`` bound to the web app's session factory and ledger. ``web`` installs it as
    ``app.state.end_account_jobs`` and the operator's delete calls it through that value: the work
    runs in the target's own ``user_session``s, never on the maint session, and guard 11 (no
    advisory lock or NOTIFY on the admin path) is about the latter."""

    async def end(user_id: UUID) -> Ended:
        return await end_jobs_of(factory, user_id, ledger)

    return end


async def delete_row(factory: Factory, ctx: UserContext) -> bool:
    """Delete the ``users`` row (the cascade does the rest); ``False`` when it was gone."""
    require_context(ctx)
    async with system_session(factory) as db:
        await user.lock_owner(db, ctx)  # no job is created between the last cancel and the delete
        deleted = await auth.delete_account(db, ctx.user_id)
        if deleted:
            await auth.record_event(db, "account_deleted")
        return deleted


async def _running(factory: Factory, ctx: UserContext) -> int:
    async with user_session(factory, ctx) as db:
        return (await db.execute(_RUNNING, {"owner_id": ctx.user_id})).scalar_one()
