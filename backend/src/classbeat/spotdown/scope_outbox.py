"""``DELETE /v2/scopes/{scope}`` for a deleted account (ADR 0040 section 7, threat model T133):
the ``spotdown_scope_deletions`` outbox (migration 0063), the only module that reads or writes it.

- **``record``** (the web app, which cannot reach spotdown: only the worker installs it) writes the
  account's ``cache_scope`` (``scope_calc.cache_scope``, an HMAC of the owner id: the id itself is
  never stored) before the ``users`` row goes: in the user's own delete in the same transaction
  (``account.erase.delete_row``), in the operator's delete just before it
  (``account.erase.make_job_ender``). It is written whether or not the account ever used
  spotdown: the web app cannot know, and a scope spotdown never saw is deleted as a no-op.
- **``drain``** (the worker's maintenance, every ``worker_maintenance_seconds``): with spotdown
  installed, takes up to ``BATCH`` due rows (``FOR UPDATE SKIP LOCKED``; each taken row's
  ``next_attempt_at`` moves on by a backoff of 1 minute doubling to an hour, so a crash or another
  worker only retries later), commits, then calls ``delete_scope`` for each and deletes the row
  on ``204``. A failure keeps the row for its next attempt; the delete is idempotent, so a repeat
  is harmless. Without spotdown installed nothing is sent, and rows older than ``KEPT_DAYS`` are
  dropped (spotdown cannot be reached from this deployment to delete anything).

Only counts are logged (by the caller); a scope is never logged.
"""

from typing import Final
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from classbeat import egress_spotdown
from classbeat.db import system_session
from classbeat.spotdown.client import Deleted, SpotdownClient
from classbeat.spotdown.scope_calc import cache_scope

BATCH: Final = 20
KEPT_DAYS: Final = 30

_RECORD = text(
    "INSERT INTO spotdown_scope_deletions (scope) VALUES (:scope) "
    "ON CONFLICT (scope) DO UPDATE SET next_attempt_at = now()"
)
_TAKE = text(
    "UPDATE spotdown_scope_deletions SET attempts = attempts + 1, "
    "next_attempt_at = now() + make_interval(secs => LEAST(60 * power(2, LEAST(attempts, 6)), "
    "3600)) "
    "WHERE scope IN (SELECT scope FROM spotdown_scope_deletions WHERE next_attempt_at <= now() "
    "ORDER BY next_attempt_at, scope LIMIT :n FOR UPDATE SKIP LOCKED) RETURNING scope"
)
_DONE = text("DELETE FROM spotdown_scope_deletions WHERE scope = :scope")
_STALE = text(
    "DELETE FROM spotdown_scope_deletions WHERE created_at <= now() - make_interval(days => :days)"
)

Factory = async_sessionmaker[AsyncSession]


async def record(db: AsyncSession, key: bytes, owner_id: UUID) -> None:
    """In the caller's transaction: the owner's scope is to be deleted in spotdown."""
    await db.execute(_RECORD, {"scope": cache_scope(key, owner_id)})


async def record_for(factory: Factory, key: bytes, owner_id: UUID) -> None:
    """``record`` in a transaction of its own."""
    async with system_session(factory) as db:
        await record(db, key, owner_id)


async def drain(factory: Factory) -> int:
    """One round: the scopes deleted in spotdown (or, without spotdown, the stale rows dropped)."""
    if not egress_spotdown.spotdown_installed():
        async with system_session(factory) as db:
            result = await db.execute(_STALE, {"days": KEPT_DAYS})
        return int(getattr(result, "rowcount", 0) or 0)
    async with system_session(factory) as db:
        taken = [str(s) for (s,) in (await db.execute(_TAKE, {"n": BATCH})).all()]
    client, deleted = SpotdownClient(), 0
    for scope in taken:
        if isinstance(await client.delete_scope(scope), Deleted):
            async with system_session(factory) as db:
                await db.execute(_DONE, {"scope": scope})
            deleted += 1
    return deleted
