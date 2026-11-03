"""Reading a job's events for the SSE stream (ADR 0005 section 6) and its draft revision (U1): one
short ``user_session`` per read, none held open between reads.

Owner-scoped twice: every statement filters on ``owner_id = ctx.user_id`` and RLS answers for the
session's user. A foreign or unknown job is ``None``, the same answer (the route turns it into 404).
The job's state is read **before** its events: a terminal state then implies its terminal event is
already committed and part of the events read next.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from classbeat.context import UserContext
from classbeat.db import user_session

_STATE = text("SELECT state FROM jobs WHERE id = :job_id AND owner_id = :owner_id")
_EVENTS = text(
    "SELECT seq, kind, payload FROM job_events "
    "WHERE job_id = :job_id AND owner_id = :owner_id AND seq > :after ORDER BY seq LIMIT :limit"
)
# ``sport``: the class's stored brief's (ADR 0018; absent = cycling), to read the content with.
_REVISION = text(
    "SELECT r.id, r.number, r.kind, r.content, r.music, r.created_at, "
    "(SELECT c.brief ->> 'sport' FROM classes c WHERE c.owner_id = r.owner_id "
    "AND c.id = r.class_id) AS sport FROM class_revisions r "
    "WHERE r.job_id = :job_id AND r.owner_id = :owner_id AND r.kind = 'draft' "
    "ORDER BY r.number DESC LIMIT 1"
)


@dataclass(frozen=True, slots=True)
class StoredEvent:
    seq: int
    kind: str
    payload: dict[str, Any]


@dataclass(frozen=True, slots=True)
class Page:
    state: str
    events: list[StoredEvent]


async def read_page(
    factory: async_sessionmaker[AsyncSession],
    ctx: UserContext,
    job_id: UUID,
    after: int,
    limit: int,
) -> Page | None:
    """The owner's job state and its events after ``after`` (at most ``limit``), or ``None``."""
    params = {"job_id": job_id, "owner_id": ctx.user_id}
    async with user_session(factory, ctx) as db:
        state = (await db.execute(_STATE, params)).scalar_one_or_none()
        if state is None:
            return None
        rows = (await db.execute(_EVENTS, {**params, "after": after, "limit": limit})).all()
    return Page(state, [StoredEvent(r.seq, r.kind, r.payload) for r in rows])


@dataclass(frozen=True, slots=True)
class StoredRevision:
    id: UUID
    number: int
    kind: str
    content: dict[str, Any]
    music: dict[str, Any]
    created_at: datetime
    sport: str | None = None  # the class's (ADR 0018); None is cycling


@dataclass(frozen=True, slots=True)
class JobRevision:
    """``revision`` is ``None`` while the (existing) job has no draft revision."""

    revision: StoredRevision | None


async def read_revision(
    factory: async_sessionmaker[AsyncSession], ctx: UserContext, job_id: UUID
) -> JobRevision | None:
    """The newest ``draft`` revision the owner's job stored (the draft summary of U1, #93), or
    ``None`` for a foreign or unknown job. Same double owner scoping as ``read_page``."""
    params = {"job_id": job_id, "owner_id": ctx.user_id}
    async with user_session(factory, ctx) as db:
        if (await db.execute(_STATE, params)).scalar_one_or_none() is None:
            return None
        row = (await db.execute(_REVISION, params)).first()
    if row is None:
        return JobRevision(None)
    return JobRevision(
        StoredRevision(
            row.id, row.number, row.kind, row.content, row.music, row.created_at, row.sport
        )
    )
