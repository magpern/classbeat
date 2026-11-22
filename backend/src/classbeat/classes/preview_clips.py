"""One audition clip for a listening session (ADR 0042 sections 6.3 to 6.5).

``clip`` runs in one user transaction, every statement filtered on ``owner_id`` as well as through
row security:

1. the audition on (``feature_off``); the user's **own session of this class**, locked
   (``preview_sessions.locked``: another user's, another class's or an unknown session is
   ``not_found``); past its ``expires_at`` it is ``ended``;
2. the class's current studio draft as its reference-keyed plan (``plan_source.load``: its
   reasons), the window placed from ``start_ms`` (``invalid_start``) and the clip's key
   (``key_calc``, with this process's renderer identity);
3. **a stored clip** under ``(owner_id, key)`` whose file is there: the delivery rule
   (``session_policy.delivery``: a clip new to the session that would pass the 25-min cap is
   ``ended``; one it already counted is free, so a ``Range`` re-request costs nothing), the count
   and ``last_used_at`` updated, and the file served (``Served``). A row whose file is gone is
   deleted and the clip is rendered again;
4. **no clip**: a new clip the session could not count is ``ended`` before anything is rendered;
   then, under the owner's advisory lock, an open ``preview_render`` job of the same key is
   reused, a job of the key that failed in the last two minutes is ``render_failed`` with its
   category, and a new job beyond ``OPEN_RENDERS`` open or ``RENDERS_PER_HOUR`` an hour is
   ``rate_limited``. Else the job is inserted with ``class_id`` NULL and ``preview_class_id`` set
   (migration 0072: outside the class's job slot) with its ``queued`` event (``Rendering``).

The job is never offered to the jobs API (its input is no ``JobInput``). Nothing of the rows is
logged.
"""

import hashlib
import json
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Final
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from classbeat.classes import preview_sessions
from classbeat.classes.class_sql import OPEN_JOB
from classbeat.context import UserContext, require_context
from classbeat.db import user_session
from classbeat.jobs.events import notify_event, validated_payload
from classbeat.jobs.user import lock_owner
from classbeat.preview import identity, plan_source, session_policy, storage
from classbeat.preview.clip_window_calc import ClipWindow, clip_window
from classbeat.preview.key_calc import KeyInputs, clip_key, plan_sha256
from classbeat.preview.render_kind import KIND

FAILED_RECENT_SECONDS: Final = 120
USER_LEASE_VERSION: Final = 0  # an event written outside any lease (``jobs.user``'s)

_CLIP = text("SELECT id FROM preview_clips WHERE owner_id = :owner_id AND key = :key FOR UPDATE")
_TOUCH = text(
    "UPDATE preview_clips SET last_used_at = now() WHERE owner_id = :owner_id AND id = :id"
)
_DROP = text("DELETE FROM preview_clips WHERE owner_id = :owner_id AND id = :id")
_OPEN_FOR_KEY = text(
    "SELECT id FROM jobs WHERE owner_id = :owner_id AND kind = :kind "  # noqa: S608 - constants
    f"AND state IN {OPEN_JOB} AND input ->> 'key' = :key LIMIT 1"
)
_FAILED_FOR_KEY = text(
    "SELECT error_category FROM jobs WHERE owner_id = :owner_id AND kind = :kind "
    "AND state = 'failed' AND input ->> 'key' = :key "
    "AND finished_at > now() - make_interval(secs => :recent) ORDER BY finished_at DESC LIMIT 1"
)
_OPEN = text(
    "SELECT count(*) FROM jobs WHERE owner_id = :owner_id AND kind = :kind "
    "AND state IN ('queued', 'running')"
)
_RECENT = text(
    "SELECT count(*) FROM jobs WHERE owner_id = :owner_id AND kind = :kind "
    "AND created_at > now() - interval '1 hour'"
)
_NEW_JOB = text(
    "INSERT INTO jobs (id, owner_id, kind, state, class_id, preview_class_id, input, "
    "idempotency_key, request_hash, credit_state, next_seq) VALUES (:id, :owner_id, :kind, "
    "'queued', NULL, :class_id, CAST(:input AS jsonb), :key, :request_hash, 'none', 1)"
)
_EVENT = text(
    "INSERT INTO job_events (id, owner_id, job_id, seq, kind, payload, lease_version) "
    "VALUES (:id, :owner_id, :job_id, 1, 'queued', CAST(:payload AS jsonb), :v)"
)
_NOTIFY_QUEUED = text("SELECT pg_notify('jobs_queued', '')")


@dataclass(frozen=True, slots=True)
class Served:
    path: Path
    window: ClipWindow


@dataclass(frozen=True, slots=True)
class Rendering:
    job_id: UUID
    window: ClipWindow


@dataclass(frozen=True, slots=True)
class Refused:
    reason: str
    category: str | None = None


async def _enqueue(
    db: AsyncSession, owner_id: UUID, class_id: UUID, window: ClipWindow, key: str
) -> UUID:
    job_id = uuid.uuid4()
    given = {
        "preview_class_id": str(class_id),
        "start_ms": window.start_ms,
        "end_ms": window.end_ms,
        "key": key,
    }
    await db.execute(
        _NEW_JOB,
        {
            "id": job_id,
            "owner_id": owner_id,
            "kind": KIND,
            "class_id": class_id,
            "input": json.dumps(given),
            "key": f"preview-render-{job_id.hex}",
            "request_hash": hashlib.sha256(f"{KIND}:{key}:{job_id}".encode()).digest(),
        },
    )
    payload = json.dumps(validated_payload("queued", {}))
    event = {"id": uuid.uuid4(), "owner_id": owner_id, "job_id": job_id, "payload": payload}
    await db.execute(_EVENT, {**event, "v": USER_LEASE_VERSION})
    await notify_event(db, job_id)
    await db.execute(_NOTIFY_QUEUED)
    return job_id


async def _render(
    db: AsyncSession, ctx: UserContext, class_id: UUID, window: ClipWindow, key: str
) -> Rendering | Refused:
    await lock_owner(db, ctx)
    params = {"owner_id": ctx.user_id, "kind": KIND, "key": key}
    open_job = (await db.execute(_OPEN_FOR_KEY, params)).scalar_one_or_none()
    if open_job is not None:
        return Rendering(open_job, window)
    recent = {**params, "recent": FAILED_RECENT_SECONDS}
    failed = (await db.execute(_FAILED_FOR_KEY, recent)).scalar_one_or_none()
    if failed is not None:
        return Refused("render_failed", str(failed))
    counts = {"owner_id": ctx.user_id, "kind": KIND}
    if int((await db.execute(_OPEN, counts)).scalar_one()) >= session_policy.OPEN_RENDERS:
        return Refused("rate_limited")
    if int((await db.execute(_RECENT, counts)).scalar_one()) >= session_policy.RENDERS_PER_HOUR:
        return Refused("rate_limited")
    return Rendering(await _enqueue(db, ctx.user_id, class_id, window, key), window)


async def clip(
    factory: async_sessionmaker[AsyncSession],
    ctx: UserContext,
    class_id: UUID,
    session_id: UUID,
    start_ms: int,
) -> Served | Rendering | Refused:
    require_context(ctx)
    async with user_session(factory, ctx) as db:
        root = storage.current()
        if root is None or not await preview_sessions.available(db):
            return Refused("feature_off")
        session = await preview_sessions.locked(db, ctx.user_id, class_id, session_id)
        if session is None:
            return Refused("not_found")
        if session_policy.expired(session.now, session.expires_at):
            return Refused("ended")
        found = await plan_source.load(db, ctx.user_id, class_id)
        if isinstance(found, str):
            return Refused(found)
        try:
            window = clip_window(found.plan, start_ms)
        except ValueError:
            return Refused("invalid_start")
        key = clip_key(
            KeyInputs(plan_sha256(found.plan), found.sources, window, identity.current())
        )
        ids = {"owner_id": ctx.user_id, "key": bytes.fromhex(key)}
        stored = (await db.execute(_CLIP, ids)).scalar_one_or_none()
        if stored is not None:
            path = storage.path_of(root, ctx.user_id, "clips", stored)
            if path.is_file() and not path.is_symlink():
                given = session_policy.delivery(
                    session.now,
                    session.expires_at,
                    session.delivered_ms,
                    session.delivered_keys,
                    key,
                    window.length_ms,
                )
                if not given.allowed:
                    return Refused("ended")
                if given.counts:
                    await preview_sessions.count(db, ctx.user_id, session, key, window.length_ms)
                await db.execute(_TOUCH, {"owner_id": ctx.user_id, "id": stored})
                return Served(path, window)
            await db.execute(_DROP, {"owner_id": ctx.user_id, "id": stored})
        counted = session.delivered_keys
        if session_policy.would_pass(session.delivered_ms, counted, key, window.length_ms):
            return Refused("ended")
        return await _render(db, ctx, class_id, window, key)
