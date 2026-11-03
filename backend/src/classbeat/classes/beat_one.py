"""Beat-one corrections of a class, for the class page (ADR 0007 amendment "beat-one
corrections", item 7, BC4).

``view`` is the class detail's ``beat_one`` block: the current revision's songs (its
``music.songs``), the class's corrections for that revision's music fingerprint, what the
revision itself applied (``music.beat_one``), whether they differ (``pending``) and how many rows
are for other music (``stale``). ``change`` sets or clears one song's correction.

Every statement names the owner and the class; the fingerprint is read from the class's current
revision inside the same transaction, with the class row locked, and never comes from the
browser. A foreign or unknown class is ``not_found`` whatever else is sent. Nothing here calls a
provider or a model, and a correction reaches MOWL only through a new revision (a ``refit_draft``
or a chat revision) that the user saves.
"""

import json
from dataclasses import dataclass, field
from typing import Any, Final
from uuid import UUID

from pydantic import ValidationError
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from classbeat.agent.beat_one import MAX_PER_CLASS, Correction
from classbeat.context import UserContext, require_context
from classbeat.db import user_session
from classbeat.design.revision import REVISION_MUSIC, RevisionMusic

Factory = async_sessionmaker[AsyncSession]

_CURRENT: Final = text(
    "SELECT c.current_revision_id, r.kind, r.music FROM classes c "
    "LEFT JOIN class_revisions r ON r.owner_id = c.owner_id AND r.class_id = c.id "
    "AND r.id = c.current_revision_id WHERE c.id = :class_id AND c.owner_id = :owner_id"
)
_LOCK: Final = text(
    "SELECT current_revision_id FROM classes WHERE id = :class_id AND owner_id = :owner_id "
    "FOR UPDATE"
)
_REVISION: Final = text(
    "SELECT kind, music FROM class_revisions "
    "WHERE id = :revision_id AND owner_id = :owner_id AND class_id = :class_id"
)
_ROWS: Final = text(
    "SELECT fingerprint, song, beats FROM beat_one_corrections "
    "WHERE owner_id = :owner_id AND class_id = :class_id ORDER BY song LIMIT :limit"
)
_UPSERT: Final = text(
    "INSERT INTO beat_one_corrections (owner_id, class_id, fingerprint, song, beats) "
    "VALUES (:owner_id, :class_id, :fingerprint, :song, :beats) "
    "ON CONFLICT ON CONSTRAINT beat_one_corrections_one_per_song "
    "DO UPDATE SET beats = EXCLUDED.beats, updated_at = now()"
)
_DELETE: Final = text(
    "DELETE FROM beat_one_corrections WHERE owner_id = :owner_id AND class_id = :class_id "
    "AND fingerprint = :fingerprint AND song = :song"
)


@dataclass(frozen=True, slots=True)
class SongView:
    song: int
    start: float
    end: float
    bpm: float
    estimate: bool
    title: str | None
    beats: int  # the class's correction for this music, 0 for none
    applied: int  # what the current revision applied, 0 for none


@dataclass(frozen=True, slots=True)
class View:
    available: bool
    revision_id: UUID | None = None
    songs: tuple[SongView, ...] = ()
    pending: bool = False
    stale: int = 0


@dataclass(frozen=True, slots=True)
class Changed:
    reason: str | None  # None, or not_found, stale_base, no_beat_data, invalid_input,
    #                      correction_limit
    view: View = field(default_factory=lambda: View(False))


def _music(kind: Any, raw: Any) -> RevisionMusic | None:
    """The current draft's music, or None when it is no draft or not readable."""
    if kind != "draft" or raw is None:
        return None
    try:
        return REVISION_MUSIC.validate_json(json.dumps(raw))
    except ValidationError:
        return None


def _build(revision_id: UUID | None, music: RevisionMusic | None, rows: list[Any]) -> View:
    if revision_id is None or music is None or not music.songs:
        return View(False, revision_id, (), False, len(rows))
    mine = {int(r.song): int(r.beats) for r in rows if r.fingerprint == music.fingerprint}
    applied = {c.song: c.beats for c in music.beat_one or []}
    songs = tuple(
        SongView(
            s.song, s.start, s.end, s.bpm, s.estimate, s.title,
            mine.get(s.song, 0), applied.get(s.song, 0),
        )
        for s in music.songs
    )  # fmt: skip
    stale = sum(1 for r in rows if r.fingerprint != music.fingerprint)
    return View(True, revision_id, songs, mine != applied, stale)


def _ids(ctx: UserContext, class_id: UUID, **more: Any) -> dict[str, Any]:
    return {"owner_id": ctx.user_id, "class_id": class_id, **more}


async def _view(db: AsyncSession, ctx: UserContext, class_id: UUID) -> View | None:
    row = (await db.execute(_CURRENT, _ids(ctx, class_id))).first()
    if row is None:
        return None
    rows = list((await db.execute(_ROWS, _ids(ctx, class_id, limit=MAX_PER_CLASS))).all())
    return _build(row.current_revision_id, _music(row.kind, row.music), rows)


async def view(factory: Factory, ctx: UserContext, class_id: UUID) -> View | None:
    """The ``beat_one`` block of the class's detail; None for a foreign or unknown class."""
    require_context(ctx)
    async with user_session(factory, ctx) as db:
        return await _view(db, ctx, class_id)


async def change(
    factory: Factory,
    ctx: UserContext,
    class_id: UUID,
    revision_id: UUID,
    song: int,
    beats: int | None,
) -> Changed:
    """Set one song's correction to ``beats`` (-3 to 3, not 0) or clear it (``None``), for the
    music of ``revision_id``, which must be the class's current revision. Idempotent."""
    require_context(ctx)
    async with user_session(factory, ctx) as db:
        locked = (await db.execute(_LOCK, _ids(ctx, class_id))).first()
        if locked is None:
            return Changed("not_found")
        found = (await db.execute(_REVISION, _ids(ctx, class_id, revision_id=revision_id))).first()
        if found is None:  # not one of this class's own revisions: as an unknown id
            return Changed("not_found")
        if locked.current_revision_id != revision_id:
            return Changed("stale_base")
        music = _music(found.kind, found.music)
        if music is None or not music.songs:
            return Changed("no_beat_data")
        if song not in {s.song for s in music.songs}:
            return Changed("invalid_input")
        params = _ids(ctx, class_id, fingerprint=music.fingerprint, song=song)
        if beats is None:
            await db.execute(_DELETE, params)
        else:
            try:
                Correction(song=song, beats=beats)
            except ValueError:
                return Changed("invalid_input")
            rows = list((await db.execute(_ROWS, _ids(ctx, class_id, limit=MAX_PER_CLASS))).all())
            exists = any(r.fingerprint == music.fingerprint and r.song == song for r in rows)
            if not exists and len(rows) >= MAX_PER_CLASS:
                return Changed("correction_limit")
            await db.execute(_UPSERT, {**params, "beats": beats})
        done = await _view(db, ctx, class_id)
    return Changed(None, done if done is not None else View(False))
