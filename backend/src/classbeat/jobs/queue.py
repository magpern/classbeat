"""Calls of the queue's ``SECURITY DEFINER`` functions (ADR 0005 section 3), the only cross-user
paths into the job tables. They run in a ``system_session`` (no ``app.user_id``) and return only
``(job_id, owner_id, lease_version)`` rows or counts; all times are the database's ``now()``."""

from collections.abc import Sequence

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from classbeat.db import system_session
from classbeat.jobs.issuer import Claimed

Factory = async_sessionmaker[AsyncSession]

_CLAIM = text(
    "SELECT job_id, owner_id, lease_version "
    "FROM public.queue_claim(:worker, :lease, CAST(:kinds AS text[]))"
)
_REAP = text("SELECT public.queue_reap(:n)")
_EXPIRE = text("SELECT public.queue_expire(:n)")
_UNSETTLED = text("SELECT job_id, owner_id, lease_version FROM public.queue_unsettled(:n)")


async def claim(
    factory: Factory, worker: str, lease_seconds: int, kinds: Sequence[str]
) -> Claimed | None:
    if not kinds:
        return None
    async with system_session(factory) as db:
        row = (
            await db.execute(
                _CLAIM, {"worker": worker, "lease": lease_seconds, "kinds": list(kinds)}
            )
        ).first()
    return None if row is None else Claimed(row.job_id, row.owner_id, row.lease_version)


async def reap(factory: Factory, limit: int = 100) -> int:
    async with system_session(factory) as db:
        return (await db.execute(_REAP, {"n": limit})).scalar_one()


async def expire(factory: Factory, limit: int = 100) -> int:
    async with system_session(factory) as db:
        return (await db.execute(_EXPIRE, {"n": limit})).scalar_one()


async def unsettled(factory: Factory, limit: int = 100) -> list[Claimed]:
    async with system_session(factory) as db:
        rows = (await db.execute(_UNSETTLED, {"n": limit})).all()
    return [Claimed(r.job_id, r.owner_id, r.lease_version) for r in rows]


_STATS = text("SELECT metric, total FROM public.queue_stats()")


async def stats(factory: Factory) -> list[tuple[str, int]]:
    """``queue_stats()``: counts per job state and of ``uncertain`` external operations, across
    all users and without ids (ADR 0005 section 7's admin seam; ``python -m classbeat jobs
    stats``)."""
    async with system_session(factory) as db:
        rows = (await db.execute(_STATS)).all()
    return [(str(r.metric), int(r.total)) for r in rows]
