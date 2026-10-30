"""The class API's transactions: reads, the current-revision pick and the class delete.

One ``user_session`` per call. Reads are fixed-size: a listing is one statement however many
classes and revisions the user has (no per-class query).
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Final
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from classbeat.classes import seams, song_views
from classbeat.context import UserContext, require_context
from classbeat.db import user_session

Factory = async_sessionmaker[AsyncSession]

LIST_LIMIT: Final = 500
REVISION_LIMIT: Final = 200
MESSAGE_LIMIT: Final = 500
OPEN_JOB: Final = "('queued', 'running', 'waiting_for_approval')"

_CLASSES = text(f"""
SELECT c.id, c.title, c.created_at, c.updated_at, c.current_revision_id,
    cur.number AS current_revision_number,
    (SELECT count(*) FROM class_revisions r
        WHERE r.owner_id = c.owner_id AND r.class_id = c.id AND r.kind = 'draft') AS revision_count,
    oj.id AS open_job_id, oj.kind AS open_job_kind, oj.state AS open_job_state,
    coalesce(sv.n, 0) AS saved_count, sv.revision_number AS saved_revision_number,
    sv.updated_at AS saved_at, (sv.content_hash = cur.content_hash) AS saved_up_to_date,
    sv.updatable_id AS saved_workout_id,
    (SELECT count(*) FROM saved_workouts d
        WHERE d.owner_id = c.owner_id AND d.class_id = c.id AND d.detached_at IS NOT NULL
    ) AS detached_count,
    coalesce(pf.profile, '[]'::jsonb) AS profile
FROM classes c
LEFT JOIN class_revisions cur
    ON cur.owner_id = c.owner_id AND cur.class_id = c.id AND cur.id = c.current_revision_id
LEFT JOIN jobs oj
    ON oj.owner_id = c.owner_id AND oj.class_id = c.id AND oj.state IN {OPEN_JOB}
LEFT JOIN LATERAL (
    SELECT jsonb_agg(jsonb_build_array((iv.i ->> 'zone')::int, (iv.i ->> 'sec')::int)
        ORDER BY sg.ord, iv.ord) AS profile
    FROM jsonb_array_elements(CASE WHEN jsonb_typeof(cur.content -> 'segments') = 'array'
        THEN cur.content -> 'segments' ELSE '[]'::jsonb END) WITH ORDINALITY AS sg(seg, ord)
    CROSS JOIN LATERAL jsonb_array_elements(CASE WHEN jsonb_typeof(sg.seg -> 'intervals') = 'array'
        THEN sg.seg -> 'intervals' ELSE '[]'::jsonb END) WITH ORDINALITY AS iv(i, ord)
    WHERE jsonb_typeof(iv.i -> 'zone') = 'number' AND jsonb_typeof(iv.i -> 'sec') = 'number'
) pf ON true
LEFT JOIN LATERAL (
    SELECT count(*) OVER () AS n, s.revision_number, s.updated_at, s.content_hash,
        CASE WHEN s.operation_id IS NULL OR EXISTS (SELECT 1 FROM external_operations o
            WHERE o.owner_id = s.owner_id AND o.id = s.operation_id AND o.class_id = s.class_id)
        THEN s.id END AS updatable_id
    FROM saved_workouts s
    WHERE s.owner_id = c.owner_id AND s.class_id = c.id AND s.detached_at IS NULL
    ORDER BY s.updated_at DESC, s.id LIMIT 1) sv ON true
WHERE c.owner_id = :owner_id AND (CAST(:class_id AS uuid) IS NULL OR c.id = CAST(:class_id AS uuid))
    AND (CAST(:after_at AS timestamptz) IS NULL OR c.updated_at < CAST(:after_at AS timestamptz)
        OR (c.updated_at = CAST(:after_at AS timestamptz) AND c.id > CAST(:after_id AS uuid)))
ORDER BY c.updated_at DESC, c.id
LIMIT :limit""")  # noqa: S608 - constants only

_REVISION_SELECT = """
SELECT r.id, r.number, r.kind, r.created_at, r.parent_id, p.number AS parent_number,
    coalesce(r.provenance ->> 'origin', 'design_draft') AS origin,
    CASE WHEN jsonb_typeof(r.provenance -> 'cost_micros') = 'number'
        THEN (r.provenance ->> 'cost_micros')::bigint ELSE 0 END AS cost_micros,
    (r.id = c.current_revision_id) AS is_current,
    s.segments, s.intervals, s.total_sec{body}
FROM class_revisions r
JOIN classes c ON c.owner_id = r.owner_id AND c.id = r.class_id
LEFT JOIN class_revisions p ON p.owner_id = r.owner_id AND p.id = r.parent_id
LEFT JOIN LATERAL (
    SELECT count(DISTINCT sg.ord) AS segments, count(iv.i) AS intervals,
        coalesce(sum((iv.i ->> 'sec')::int), 0) AS total_sec
    FROM jsonb_array_elements(CASE WHEN jsonb_typeof(r.content -> 'segments') = 'array'
        THEN r.content -> 'segments' ELSE '[]'::jsonb END) WITH ORDINALITY AS sg(seg, ord)
    LEFT JOIN LATERAL jsonb_array_elements(CASE WHEN jsonb_typeof(sg.seg -> 'intervals') = 'array'
        THEN sg.seg -> 'intervals' ELSE '[]'::jsonb END) AS iv(i) ON true) s ON true
WHERE r.owner_id = :owner_id AND r.class_id = :class_id AND r.kind = 'draft'
    AND (CAST(:revision_id AS uuid) IS NULL OR r.id = CAST(:revision_id AS uuid))
ORDER BY r.number
LIMIT :limit"""
_REVISIONS = text(_REVISION_SELECT.format(body=""))
_REVISION = text(_REVISION_SELECT.format(body=", r.content, r.music, r.provenance"))

_CLASS_EXISTS = text("SELECT 1 FROM classes WHERE id = :class_id AND owner_id = :owner_id")
_MESSAGES = text(
    "SELECT seq, role, body, job_id, base_revision_id, result_revision_id, change, created_at "
    "FROM class_messages WHERE owner_id = :owner_id AND class_id = :class_id "
    "ORDER BY seq LIMIT :limit"
)
_LOCK_CLASS = text(
    "SELECT current_revision_id FROM classes WHERE id = :class_id AND owner_id = :owner_id "
    "FOR UPDATE"
)
_OPEN_JOB = text(
    "SELECT 1 FROM jobs WHERE owner_id = :owner_id AND class_id = :class_id "  # noqa: S608 - constants only
    f"AND state IN {OPEN_JOB} LIMIT 1"
)
_PICKABLE = text(
    "SELECT content_hash FROM class_revisions WHERE id = :revision_id AND owner_id = :owner_id "
    "AND class_id = :class_id AND kind = 'draft'"
)
_SET_CURRENT = text(
    "UPDATE classes SET current_revision_id = :revision_id, updated_at = now() "
    "WHERE id = :class_id AND owner_id = :owner_id"
)
_WRITES = text(
    f"SELECT state, (state = 'uncertain' AND {seams.NOT_ACTIONABLE_SQL}) AS inside_bound "  # noqa: S608 - constants only
    "FROM external_operations WHERE owner_id = :owner_id AND class_id = :class_id "
    "AND kind IN ('mowl_save_workout', 'mowl_update_workout') "
    "AND state IN ('pending', 'uncertain')"
)
_KEPT = text(
    "SELECT count(*) FROM saved_workouts WHERE owner_id = :owner_id AND class_id = :class_id "
    "AND detached_at IS NULL"
)
_DELETE = text("DELETE FROM classes WHERE id = :class_id AND owner_id = :owner_id")


def _ids(ctx: UserContext, class_id: UUID | None, **more: Any) -> dict[str, Any]:
    return {
        "owner_id": ctx.user_id,
        "class_id": class_id,
        "after_at": None,
        "after_id": None,
        **more,
    }


Cursor = tuple[datetime, UUID]  # (updated_at, id) of the last class of the page before


@dataclass(frozen=True, slots=True)
class ClassPage:
    rows: list[Any]
    next_after: Cursor | None  # None on the last page


async def list_classes(
    factory: Factory, ctx: UserContext, *, limit: int = LIST_LIMIT, after: Cursor | None = None
) -> ClassPage:
    """One page of at most ``limit`` (and ``LIST_LIMIT``) classes, newest first, after the
    keyset ``after``. One extra row is read to know whether another page follows."""
    require_context(ctx)
    size = max(1, min(limit, LIST_LIMIT))
    after_at, after_id = after if after is not None else (None, None)
    params = _ids(ctx, None, limit=size + 1, after_at=after_at, after_id=after_id)
    async with user_session(factory, ctx) as db:
        rows = list((await db.execute(_CLASSES, params)).all())
    if len(rows) <= size:
        return ClassPage(rows, None)
    rows = rows[:size]
    return ClassPage(rows, (rows[-1].updated_at, rows[-1].id))


async def get_class(factory: Factory, ctx: UserContext, class_id: UUID) -> Any | None:
    """The class row, or ``None`` for a foreign or unknown id. Orphaned writes are resolved first
    (ADR 0007 section 8: every route that reads a class's operations)."""
    require_context(ctx)
    async with user_session(factory, ctx) as db:
        await seams.resolve_orphans(db, ctx)
        return (await db.execute(_CLASSES, _ids(ctx, class_id, limit=1))).first()


async def get_class_detail(
    factory: Factory, ctx: UserContext, class_id: UUID
) -> tuple[Any, song_views.SongsState, song_views.Playlists] | None:
    """The class row with its song state and playlists (one transaction), or ``None``."""
    require_context(ctx)
    async with user_session(factory, ctx) as db:
        await seams.resolve_orphans(db, ctx)
        row = (await db.execute(_CLASSES, _ids(ctx, class_id, limit=1))).first()
        if row is None:
            return None
        songs = await song_views.songs_state(db, ctx.user_id, class_id)
        return row, songs, await song_views.playlists_of(db, ctx.user_id, class_id)


async def list_revisions(factory: Factory, ctx: UserContext, class_id: UUID) -> list[Any] | None:
    require_context(ctx)
    async with user_session(factory, ctx) as db:
        if (await db.execute(_CLASS_EXISTS, _ids(ctx, class_id))).first() is None:
            return None
        params = _ids(ctx, class_id, revision_id=None, limit=REVISION_LIMIT)
        return list((await db.execute(_REVISIONS, params)).all())


async def get_revision(
    factory: Factory, ctx: UserContext, class_id: UUID, revision_id: UUID
) -> Any | None:
    """A ``draft`` revision of the user's own class, or ``None``."""
    require_context(ctx)
    async with user_session(factory, ctx) as db:
        params = _ids(ctx, class_id, revision_id=revision_id, limit=1)
        return (await db.execute(_REVISION, params)).first()


async def list_messages(factory: Factory, ctx: UserContext, class_id: UUID) -> list[Any] | None:
    require_context(ctx)
    async with user_session(factory, ctx) as db:
        if (await db.execute(_CLASS_EXISTS, _ids(ctx, class_id))).first() is None:
            return None
        return list((await db.execute(_MESSAGES, _ids(ctx, class_id, limit=MESSAGE_LIMIT))).all())


@dataclass(frozen=True, slots=True)
class Outcome:
    """``reason`` is ``None`` when the call went through; ``kept`` is, for a delete, the number
    of MOWL workouts classbeat made that stay in the user's account."""

    reason: str | None = None
    kept: int = 0
    playlists: tuple[song_views.PlaylistRef, ...] = ()  # a delete: left in the user's Spotify


async def pick(
    factory: Factory, ctx: UserContext, class_id: UUID, revision_id: UUID, content_hash: bytes
) -> Outcome:
    """ADR 0007 section 2: one transaction, the class row locked, so a pick can't race a store.

    ``not_found`` (the class, or a revision that is not a draft of this class), ``hash_mismatch``,
    ``class_busy`` (an open job). Picking the current revision again writes nothing.
    """
    require_context(ctx)
    async with user_session(factory, ctx) as db:
        locked = (await db.execute(_LOCK_CLASS, _ids(ctx, class_id))).first()
        if locked is None:
            return Outcome("not_found")
        found = (await db.execute(_PICKABLE, _ids(ctx, class_id, revision_id=revision_id))).first()
        if found is None:
            return Outcome("not_found")
        if bytes(found.content_hash) != content_hash:
            return Outcome("hash_mismatch")
        if locked.current_revision_id == revision_id:
            return Outcome()
        if (await db.execute(_OPEN_JOB, _ids(ctx, class_id))).first() is not None:
            return Outcome("class_busy")
        await db.execute(_SET_CURRENT, _ids(ctx, class_id, revision_id=revision_id))
    return Outcome()


async def delete(
    factory: Factory, ctx: UserContext, class_id: UUID, *, confirm_uncertain: bool
) -> Outcome:
    """ADR 0007 sections 8 and 11. Orphaned writes are resolved first; the class row is locked
    (no job or operation of the class can be inserted meanwhile); then, in this order,
    ``class_busy`` (an open job), ``call_may_be_running`` (a save or update row ``pending`` or
    ``uncertain`` and inside its bound), ``uncertain_write`` (an ``uncertain`` row, unless
    confirmed). A refusal returns normally, so what ``resolve_orphans`` marked stays marked.
    The cascade is the schema's (migrations 0004 and 0005); ``agent_runs`` keeps its rows."""
    require_context(ctx)
    async with user_session(factory, ctx) as db:
        await seams.resolve_orphans(db, ctx)
        if (await db.execute(_LOCK_CLASS, _ids(ctx, class_id))).first() is None:
            return Outcome("not_found")
        if (await db.execute(_OPEN_JOB, _ids(ctx, class_id))).first() is not None:
            return Outcome("class_busy")
        writes = (await db.execute(_WRITES, _ids(ctx, class_id))).all()
        if any(w.state == "pending" or w.inside_bound for w in writes):
            return Outcome("call_may_be_running")
        if writes and not confirm_uncertain:
            return Outcome("uncertain_write")
        kept = int((await db.execute(_KEPT, _ids(ctx, class_id))).scalar_one())
        left = (await song_views.playlists_of(db, ctx.user_id, class_id)).all
        await db.execute(_DELETE, _ids(ctx, class_id))
    return Outcome(None, kept, left)
