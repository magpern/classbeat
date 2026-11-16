"""A studio revision's Spotify sync state and "Sync again" (ADR 0036 section 9, SR C8).

- ``view``: the ``working_copy`` and ``final`` states of one revision of the user's class, each
  ``{state, detail}`` or None (never synced). No playlist id, no track id.
- ``sync_again``: in one user transaction, under the class row's lock: the studio on and the
  user's class (``not_found``), the row (``not_found``), still ``failed`` as ``sync.shown``
  sees it, a ``pending`` row with no job that could still write it included (``not_failed``: a
  synced row, or a pending one being written, has nothing to do, so a repeated press starts
  nothing), a Spotify
  connection (``not_connected``); then the row is intended again (``pending``, a new ``seq``:
  it becomes the playlist's intended revision) and a ``spotify_sync`` job is inserted with
  ``class_id`` NULL (it runs beside the class's own open job) and its queued event.

Reasons are codes only; nothing of the rows is logged.
"""

import hashlib
import json
import uuid
from dataclasses import dataclass
from typing import Any, Final
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from classbeat.classes.class_sql import LOCK_CLASS
from classbeat.context import UserContext, require_context
from classbeat.db import user_session
from classbeat.jobs.events import notify_event, validated_payload
from classbeat.playlists import sync
from classbeat.swedebeat_studio.admit import studio_on
from classbeat.swedebeat_studio.sync_kind import KIND

USER_LEASE_VERSION: Final = 0  # an event written outside any lease (``jobs.user``'s)

_CLASS = text("SELECT 1 FROM classes WHERE id = :class_id AND owner_id = :owner_id")
_NEW_JOB = text(
    "INSERT INTO jobs (id, owner_id, kind, state, class_id, input, idempotency_key, "
    "request_hash, credit_state, next_seq) VALUES (:id, :owner_id, :kind, 'queued', NULL, "
    "CAST(:input AS jsonb), :key, :request_hash, 'none', 1)"
)
_EVENT = text(
    "INSERT INTO job_events (id, owner_id, job_id, seq, kind, payload, lease_version) "
    "VALUES (:id, :owner_id, :job_id, 1, 'queued', CAST(:payload AS jsonb), :v)"
)
_NOTIFY_QUEUED = text("SELECT pg_notify('jobs_queued', '')")


@dataclass(frozen=True, slots=True)
class Again:
    reason: str | None = None
    job_id: UUID | None = None


async def view(
    factory: async_sessionmaker[AsyncSession], ctx: UserContext, class_id: UUID, revision_id: UUID
) -> dict[str, Any] | str:
    require_context(ctx)
    async with user_session(factory, ctx) as db:
        if not await studio_on(db):
            return "feature_off"
        ids = {"class_id": class_id, "owner_id": ctx.user_id}
        if (await db.execute(_CLASS, ids)).first() is None:
            return "not_found"
        found: dict[str, Any] = {}
        for target in sync.TARGETS:
            row = await sync.load(db, ctx.user_id, class_id, revision_id, target)
            seen = None if row is None else await sync.shown(db, ctx.user_id, row)
            found[target] = sync.view(seen)
    return found


async def _enqueue(db: AsyncSession, owner_id: UUID, found: sync.Sync) -> UUID:
    job_id = uuid.uuid4()
    given = {
        "source_class_id": str(found.class_id),
        "revision_id": str(found.revision_id),
        "target": found.target,
    }
    marker = f"{KIND}:{found.id}:{job_id}".encode()
    await db.execute(
        _NEW_JOB,
        {
            "id": job_id,
            "owner_id": owner_id,
            "kind": KIND,
            "input": json.dumps(given),
            "key": f"spotify-sync-{job_id.hex}",
            "request_hash": hashlib.sha256(marker).digest(),
        },
    )
    payload = json.dumps(validated_payload("queued", {}))
    await db.execute(
        _EVENT,
        {
            "id": uuid.uuid4(),
            "owner_id": owner_id,
            "job_id": job_id,
            "payload": payload,
            "v": USER_LEASE_VERSION,
        },
    )
    await notify_event(db, job_id)
    await db.execute(_NOTIFY_QUEUED)
    return job_id


async def sync_again(
    factory: async_sessionmaker[AsyncSession],
    ctx: UserContext,
    class_id: UUID,
    revision_id: UUID,
    target: sync.Target,
) -> Again:
    require_context(ctx)
    async with user_session(factory, ctx) as db:
        if not await studio_on(db):
            return Again("feature_off")
        ids = {"class_id": class_id, "owner_id": ctx.user_id}
        if (await db.execute(LOCK_CLASS, ids)).first() is None:
            return Again("not_found")
        found = await sync.load(db, ctx.user_id, class_id, revision_id, target, lock=True)
        if found is None:
            return Again("not_found")
        if (await sync.shown(db, ctx.user_id, found)).state != "failed":
            return Again("not_failed")
        if ctx.connection("spotify") is None:
            return Again("not_connected")
        if not await sync.again(db, ctx.user_id, found.id):
            return Again("not_failed")
        job_id = await _enqueue(db, ctx.user_id, found)
    return Again(None, job_id)
