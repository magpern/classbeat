"""The class API's transactions: reads and the current-revision pick (the class delete:
``classbeat.classes.removal``; the dismissed rules: ``classbeat.classes.dismissals``).

One ``user_session`` per call. Reads are fixed-size: a listing is one statement however many
classes and revisions the user has (no per-class query).
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Final
from uuid import UUID

from pydantic import ValidationError
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from classbeat.agent.brief import Brief
from classbeat.classes import change_view, match_view, seams, song_views
from classbeat.classes.class_list_sql import CLASSES
from classbeat.classes.class_sql import LOCK_CLASS, OPEN_JOB_SQL
from classbeat.classes.composer_view import ComposerState, composer_state
from classbeat.classes.dismissals import set_dismissed as set_dismissed  # the routes' name
from classbeat.classes.kinds import SONG_DESIGN_KIND
from classbeat.classes.messages import list_messages as list_messages  # the routes' name
from classbeat.classes.selection import belongs_to_draft
from classbeat.context import UserContext, require_context
from classbeat.db import user_session
from classbeat.design.games_policy import RETIRED, has_retired_game
from classbeat.design.revision_ready import revision_ready
from classbeat.player_send.class_state import SentState, sent_state
from classbeat.quota.gates import check_stored_draft

Factory = async_sessionmaker[AsyncSession]

LIST_LIMIT: Final = 500
REVISION_LIMIT: Final = 200


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

_BRIEF = text(
    "SELECT coalesce(c.brief, (SELECT j.input -> 'brief' FROM jobs j "  # noqa: S608 - constants only
    f"WHERE j.owner_id = c.owner_id AND j.class_id = c.id AND j.kind = '{SONG_DESIGN_KIND}' "
    "ORDER BY j.created_at DESC LIMIT 1)) FROM classes c "
    "WHERE c.owner_id = :owner_id AND c.id = :class_id"
)
_CLASS_EXISTS = text("SELECT 1 FROM classes WHERE id = :class_id AND owner_id = :owner_id")
_PICKABLE = text(
    "SELECT content_hash, content FROM class_revisions WHERE id = :revision_id "
    "AND owner_id = :owner_id AND class_id = :class_id AND kind = 'draft'"
)
_SET_CURRENT = text(
    "UPDATE classes SET current_revision_id = :revision_id, updated_at = now() "
    "WHERE id = :class_id AND owner_id = :owner_id"
)
# The class row is already locked, so this and ``_SET_CURRENT`` commit together.
_CLEAR_MATCH = text(
    "UPDATE classes SET approved_swedebeat_match_id = NULL "
    "WHERE id = :class_id AND owner_id = :owner_id"
)


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
        rows = list((await db.execute(CLASSES, params)).all())
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
        return (await db.execute(CLASSES, _ids(ctx, class_id, limit=1))).first()


@dataclass(frozen=True)
class ClassDetail:
    row: Any
    songs: song_views.SongsState
    playlists: song_views.Playlists
    brief: Brief | None
    match: match_view.MatchPointer | None = None  # only read while the Swedebeat flag is on
    player: SentState | None = None  # the revision the connected player holds (#1068)
    composer: ComposerState = field(default_factory=ComposerState)  # ADR 0038 (#1188)
    can_change: bool = False  # the history's value (#1484)

    def fields(self) -> dict[str, Any]:
        """The class answer's composer fields and ``can_change`` (``ClassDetailOut``)."""
        return {**self.composer.fields(), "can_change": self.can_change}


async def stored_brief(db: AsyncSession, owner_id: UUID, class_id: UUID) -> Brief | None:
    """The class's stored brief (ADR 0008 section 6): ``classes.brief`` once it has a design;
    before that, the brief of the class's newest ``design_with_songs`` job (the one its songs
    were picked for). The user's own rows only; None when neither is there or it no longer
    parses."""
    value = (
        await db.execute(_BRIEF, {"owner_id": owner_id, "class_id": class_id})
    ).scalar_one_or_none()
    if value is None:
        return None
    try:
        return Brief.model_validate(value)
    except ValidationError:
        return None


async def get_class_detail(
    factory: Factory, ctx: UserContext, class_id: UUID, *, with_match: bool = False
) -> ClassDetail | None:
    """The class row with its song state, playlists and stored brief (one transaction), or
    ``None``. ``with_match`` (the effective ``swedebeat_enabled`` flag) adds the pointer to the
    newest ``swedebeat_match``; without it no match row is read."""
    require_context(ctx)
    async with user_session(factory, ctx) as db:
        await seams.resolve_orphans(db, ctx)
        row = (await db.execute(CLASSES, _ids(ctx, class_id, limit=1))).first()
        if row is None:
            return None
        songs = await song_views.songs_state(db, ctx.user_id, class_id)
        playlists = await song_views.playlists_of(db, ctx.user_id, class_id)
        brief = await stored_brief(db, ctx.user_id, class_id)
        match = await match_view.newest_match(db, ctx.user_id, class_id) if with_match else None
        player = await sent_state(db, ctx.user_id, class_id)
        composer = await composer_state(db, ctx.user_id, class_id)
        change = bool(await change_view.can_change(db, ctx, class_id))
        return ClassDetail(row, songs, playlists, brief, match, player, composer, change)


_DISMISSED = text(
    "SELECT dismissed_rules FROM classes WHERE id = :class_id AND owner_id = :owner_id"
)


async def get_pattern_inputs(
    factory: Factory, ctx: UserContext, class_id: UUID
) -> tuple[Brief | None, list[str]]:
    """The stored brief and the dismissed rule ids of the user's own class (ADR 0017 section 4)."""
    require_context(ctx)
    async with user_session(factory, ctx) as db:
        raw = (await db.execute(_DISMISSED, _ids(ctx, class_id))).scalar_one_or_none()
        dismissed = [r for r in raw or [] if isinstance(r, str)]
        return await stored_brief(db, ctx.user_id, class_id), dismissed


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


@dataclass(frozen=True, slots=True)
class Outcome:
    """``reason`` is ``None`` when the call went through."""

    reason: str | None = None
    current_revision_id: UUID | None = None  # with ``stale_revision``


async def pick(
    factory: Factory,
    ctx: UserContext,
    class_id: UUID,
    revision_id: UUID,
    content_hash: bytes,
    expected_current_id: UUID | None = None,
) -> Outcome:
    """ADR 0007 section 2: one transaction, the class row locked, so a pick can't race a store.

    ``not_found`` (the class, or a revision that is not a draft of this class), ``hash_mismatch``,
    ``class_busy`` (an open job), ``game_retired`` (a game MOWL no longer offers), ``not_ready``
    (a sketch, ``design.revision_ready``, before anything else is read). Picking the
    current revision again writes nothing. With ``expected_current_id`` (an undo or redo, ADR 0042
    section 6) a class whose current revision is another is ``stale_revision`` with that id,
    before anything else.

    No model runs here, so the draft is checked against the user's **current** plan (#496): an
    older revision with a game, an FTP test or a length the plan does not include now (one made
    before a downgrade or an admin's change) raises ``QuotaRefused`` (409 ``quota`` ``feature`` or
    ``length``) and nothing changes.

    A stored Swedebeat match stays only when it is the chosen draft's song list. Any other draft
    clears ``approved_swedebeat_match_id`` in this same transaction. Same owner is not enough.
    """
    require_context(ctx)
    async with user_session(factory, ctx) as db:
        locked = (await db.execute(LOCK_CLASS, _ids(ctx, class_id))).first()
        if locked is None:
            return Outcome("not_found")
        current = locked.current_revision_id
        if expected_current_id is not None and current != expected_current_id:
            return Outcome("stale_revision", current_revision_id=current)
        not_ready = await revision_ready(db, ctx.user_id, class_id, revision_id)
        if not_ready is not None:
            return Outcome(not_ready)  # a sketch is never picked, current or not (ADR 0038)
        found = (await db.execute(_PICKABLE, _ids(ctx, class_id, revision_id=revision_id))).first()
        if found is None:
            return Outcome("not_found")
        if bytes(found.content_hash) != content_hash:
            return Outcome("hash_mismatch")
        if locked.current_revision_id == revision_id:
            return Outcome()
        if (await db.execute(OPEN_JOB_SQL, _ids(ctx, class_id))).first() is not None:
            return Outcome("class_busy")
        if not isinstance(found.content, dict):
            return Outcome("not_found")  # never a draft: fail closed
        if has_retired_game(found.content):
            return Outcome(RETIRED)  # ADR 0029 section 3: a legacy draft never becomes current
        await check_stored_draft(db, ctx, found.content)  # pyright: ignore[reportUnknownArgumentType]
        match_id = locked.approved_swedebeat_match_id
        keep = isinstance(match_id, UUID) and await belongs_to_draft(
            db, ctx.user_id, class_id, revision_id, match_id
        )
        await db.execute(_SET_CURRENT, _ids(ctx, class_id, revision_id=revision_id))
        if not keep:
            await db.execute(_CLEAR_MATCH, _ids(ctx, class_id))
    return Outcome()
