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
from classbeat.design import music_kind
from classbeat.design.revision import REVISION_MUSIC, RevisionMusic

Factory = async_sessionmaker[AsyncSession]

_CURRENT: Final = text(
    "SELECT c.current_revision_id, c.music_source, r.kind, r.music FROM classes c "
    "LEFT JOIN class_revisions r ON r.owner_id = c.owner_id AND r.class_id = c.id "
    "AND r.id = c.current_revision_id WHERE c.id = :class_id AND c.owner_id = :owner_id"
)
_LOCK: Final = text(
    "SELECT current_revision_id, music_source FROM classes "
    "WHERE id = :class_id AND owner_id = :owner_id FOR UPDATE"
)
_REVISION: Final = text(
    "SELECT kind, music FROM class_revisions "
    "WHERE id = :revision_id AND owner_id = :owner_id AND class_id = :class_id"
)
# Every row of the class: the current fingerprint's, and the stale ones (other music).
_ROWS: Final = text(
    "SELECT id, fingerprint, song, beats, bpm FROM beat_one_corrections "
    "WHERE owner_id = :owner_id AND class_id = :class_id ORDER BY song, created_at, id"
)
_COUNT: Final = text(
    "SELECT count(*) FROM beat_one_corrections WHERE owner_id = :owner_id "
    "AND class_id = :class_id AND fingerprint = :fingerprint"
)
_EXISTS: Final = text(
    "SELECT 1 FROM beat_one_corrections WHERE owner_id = :owner_id AND class_id = :class_id "
    "AND fingerprint = :fingerprint AND song = :song"
)
_UPSERT: Final = text(
    "INSERT INTO beat_one_corrections (owner_id, class_id, fingerprint, song, beats, bpm) "
    "VALUES (:owner_id, :class_id, :fingerprint, :song, :beats, :bpm) "
    "ON CONFLICT ON CONSTRAINT beat_one_corrections_one_per_song "
    "DO UPDATE SET beats = EXCLUDED.beats, bpm = EXCLUDED.bpm, updated_at = now()"
)
_DELETE_SONG: Final = text(
    "DELETE FROM beat_one_corrections WHERE owner_id = :owner_id AND class_id = :class_id "
    "AND fingerprint = :fingerprint AND song = :song"
)
_DELETE_ROW: Final = text(
    "DELETE FROM beat_one_corrections WHERE owner_id = :owner_id AND class_id = :class_id "
    "AND id = :row_id RETURNING id"
)
_DELETE_ALL: Final = text(
    "DELETE FROM beat_one_corrections WHERE owner_id = :owner_id AND class_id = :class_id"
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
    bpm_changed: bool  # the song's BPM is not the one the correction was set at


@dataclass(frozen=True, slots=True)
class StaleRow:
    id: UUID
    song: int
    beats: int


@dataclass(frozen=True, slots=True)
class View:
    available: bool
    revision_id: UUID | None = None
    songs: tuple[SongView, ...] = ()
    pending: bool = False
    stale: tuple[StaleRow, ...] = ()


@dataclass(frozen=True, slots=True)
class Changed:
    # None, or not_found, stale_base, no_beat_data, not_changeable, invalid_input,
    # correction_limit
    reason: str | None
    view: View = field(default_factory=lambda: View(False))


def _music(kind: Any, raw: Any) -> RevisionMusic | None:
    """The current draft's music, or None when it is no draft, has no beat data or is not
    readable (a soundless revision's ``{"source": "none"}`` among them)."""
    if kind != "draft" or raw is None or music_kind.is_none(raw):
        return None
    try:
        return REVISION_MUSIC.validate_json(json.dumps(raw))
    except ValidationError:
        return None


def _bpm_changed(stored: Any, now: float) -> bool:
    """Whether the song's BPM is not the one the row was set at. The column is single-precision
    (128.3 reads back as 128.30000305) and ``songs`` holds one decimal, so both are compared
    after rounding to one decimal; any real change of tempo shows."""
    return round(float(stored), 1) != round(float(now), 1)


def _usable(source: Any) -> bool:
    """A class whose music can be corrected: neither soundless nor without a music source."""
    return source is not None and not music_kind.is_none(source)


def _build(
    revision_id: UUID | None, source: Any, music: RevisionMusic | None, rows: list[Any]
) -> View:
    every = tuple(StaleRow(r.id, int(r.song), int(r.beats)) for r in rows)
    if revision_id is None or not _usable(source) or music is None or not music.songs:
        return View(False, revision_id, (), False, every)
    played = {s.song for s in music.songs}
    mine = {int(r.song): r for r in rows if r.fingerprint == music.fingerprint}
    applied = {c.song: c.beats for c in music.beat_one or []}
    songs = tuple(
        SongView(
            s.song, s.start, s.end, s.bpm, s.estimate, s.title,
            int(mine[s.song].beats) if s.song in mine else 0,
            applied.get(s.song, 0),
            s.song in mine and _bpm_changed(mine[s.song].bpm, s.bpm),
        )
        for s in music.songs
    )  # fmt: skip
    wanted = {song: int(r.beats) for song, r in mine.items() if song in played}
    stale = tuple(
        StaleRow(r.id, int(r.song), int(r.beats))
        for r in rows
        if r.fingerprint != music.fingerprint or int(r.song) not in played
    )
    return View(True, revision_id, songs, wanted != applied, stale)


def _ids(ctx: UserContext, class_id: UUID, **more: Any) -> dict[str, Any]:
    return {"owner_id": ctx.user_id, "class_id": class_id, **more}


async def _view(db: AsyncSession, ctx: UserContext, class_id: UUID) -> View | None:
    row = (await db.execute(_CURRENT, _ids(ctx, class_id))).first()
    if row is None:
        return None
    rows = list((await db.execute(_ROWS, _ids(ctx, class_id))).all())
    return _build(row.current_revision_id, row.music_source, _music(row.kind, row.music), rows)


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
        if music_kind.is_none(locked.music_source):  # soundless: nothing to correct
            return Changed("no_beat_data")
        if locked.music_source is None:  # a #550 copy of picked songs: no music to fit to
            return Changed("not_changeable")
        if locked.current_revision_id != revision_id:
            return Changed("stale_base")
        music = _music(found.kind, found.music)
        if music is None or not music.songs:
            return Changed("no_beat_data")
        by_song = {s.song: s for s in music.songs}
        if song not in by_song:
            return Changed("invalid_input")
        params = _ids(ctx, class_id, fingerprint=music.fingerprint, song=song)
        if beats is None:
            await db.execute(_DELETE_SONG, params)
        else:
            try:
                Correction(song=song, beats=beats)
            except ValueError:
                return Changed("invalid_input")
            exists = (await db.execute(_EXISTS, params)).first() is not None
            count = (await db.execute(_COUNT, params)).scalar_one()
            if not exists and count >= MAX_PER_CLASS:  # the current fingerprint's rows only
                return Changed("correction_limit")
            await db.execute(_UPSERT, {**params, "beats": beats, "bpm": by_song[song].bpm})
        done = await _view(db, ctx, class_id)
    return Changed(None, done if done is not None else View(False))


async def clear_row(factory: Factory, ctx: UserContext, class_id: UUID, row_id: UUID) -> Changed:
    """Clear any row of the class by its id (keyed by owner, class and id): a row of another
    class, or another user's, deletes nothing and is ``not_found``."""
    require_context(ctx)
    async with user_session(factory, ctx) as db:
        if (await db.execute(_LOCK, _ids(ctx, class_id))).first() is None:
            return Changed("not_found")
        gone = (await db.execute(_DELETE_ROW, _ids(ctx, class_id, row_id=row_id))).first()
        if gone is None:
            return Changed("not_found")
        done = await _view(db, ctx, class_id)
    return Changed(None, done if done is not None else View(False))


async def clear_all(factory: Factory, ctx: UserContext, class_id: UUID) -> Changed:
    """Clear every row of the class. Idempotent."""
    require_context(ctx)
    async with user_session(factory, ctx) as db:
        if (await db.execute(_LOCK, _ids(ctx, class_id))).first() is None:
            return Changed("not_found")
        await db.execute(_DELETE_ALL, _ids(ctx, class_id))
        done = await _view(db, ctx, class_id)
    return Changed(None, done if done is not None else View(False))
