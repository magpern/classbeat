"""The Spotify draft's alignment facts and the instructor's corrections of a class (ADR 0042
sections 2 and 7.4; CP-S3, #1398), for the routes of
``classbeat.classes.spotify_corrections_routes``. Web only. The rules are
``spotify_alignment_model``'s.

``view`` is what the listening preview needs to map an observed Spotify position to the class:
each song's length, spotdown's alignment fact (never changed here) and the class's correction
beside it (never merged with it), with the draft's crossfade constant ``C`` and ``map_version``.
``set_offset`` and ``clear_offset`` write one song's correction.

Class-owned like ``beat_one``: every statement names the owner and the class, the music
fingerprint is read from the class's current revision in the same transaction with the class row
locked (never from the browser), and another user's class is ``not_found`` whatever else is sent.
Only a draft with an approved song list has a view (``no_draft`` otherwise, as the audition).

A correction only moves the audition cursor. It never touches a revision, an analysis or an
approval: it reaches the class only through a refit that stores a new ``draft`` revision.

Refusals: ``not_found``, ``no_draft``, ``not_ready`` (a sketch), ``stale_base`` (the page's draft
is no longer current), ``invalid_input`` (a song the draft does not play, or an offset out of
range), ``no_analysis`` (a song with no analysed file to set a correction against) and
``correction_limit`` (200 rows for the current music). A revision that is not one of the
class's own is ``not_found``, as an unknown id.
"""

import json
from dataclasses import dataclass
from typing import Any, Final
from uuid import UUID

from pydantic import ValidationError
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from classbeat.classes import spotify_alignment_model as model
from classbeat.classes.spotify_alignment_model import CorrectionState, Fact, SetAgainst
from classbeat.context import UserContext, require_context
from classbeat.db import user_session
from classbeat.design.revision import SpotifyRevisionMusic
from classbeat.design.revision_ready import revision_ready
from classbeat.songs import song_list

Factory = async_sessionmaker[AsyncSession]

MAX_ROWS: Final = 200  # corrections of one class's current music (the column's song bound)

_CURRENT: Final = text(
    "SELECT c.current_revision_id AS current, r.kind, r.music, r.provenance FROM classes c "
    "LEFT JOIN class_revisions r ON r.owner_id = c.owner_id AND r.class_id = c.id "
    "AND r.id = c.current_revision_id WHERE c.id = :class_id AND c.owner_id = :owner_id"
)
_LOCK: Final = text(
    "SELECT current_revision_id AS current FROM classes "
    "WHERE id = :class_id AND owner_id = :owner_id FOR UPDATE"
)
_REVISION: Final = text(
    "SELECT 1 FROM class_revisions WHERE id = :revision_id AND owner_id = :owner_id "
    "AND class_id = :class_id"
)
_ROWS: Final = text(
    "SELECT song, offset_ms, set_against_analysis FROM spotify_offset_corrections "
    "WHERE owner_id = :owner_id AND class_id = :class_id AND fingerprint = :fingerprint"
)
_EXISTS: Final = text(
    "SELECT 1 FROM spotify_offset_corrections WHERE owner_id = :owner_id AND class_id = :class_id "
    "AND fingerprint = :fingerprint AND song = :song"
)
_COUNT: Final = text(
    "SELECT count(*) FROM spotify_offset_corrections WHERE owner_id = :owner_id "
    "AND class_id = :class_id AND fingerprint = :fingerprint"
)
_UPSERT: Final = text(
    "INSERT INTO spotify_offset_corrections "
    "(owner_id, class_id, fingerprint, song, offset_ms, set_against_analysis) "
    "VALUES (:owner_id, :class_id, :fingerprint, :song, :offset_ms, "
    "CAST(:against AS jsonb)) "
    "ON CONFLICT ON CONSTRAINT spotify_offset_corrections_one_per_song "
    "DO UPDATE SET offset_ms = EXCLUDED.offset_ms, "
    "set_against_analysis = EXCLUDED.set_against_analysis, updated_at = now()"
)
_DELETE: Final = text(
    "DELETE FROM spotify_offset_corrections WHERE owner_id = :owner_id AND class_id = :class_id "
    "AND fingerprint = :fingerprint AND song = :song"
)


@dataclass(frozen=True, slots=True)
class CorrectionView:
    offset_ms: int
    set_against: SetAgainst | None
    state: CorrectionState


@dataclass(frozen=True, slots=True)
class SongView:
    piece: int  # 0-based play order, as the audition's ``piece_index``
    song: int  # 1-based, as the correction's ``song``
    title: str
    artist: str
    length_ms: int
    fact: Fact | None
    correction: CorrectionView | None


@dataclass(frozen=True, slots=True)
class View:
    revision_id: UUID
    map_version: int | None
    crossfade_ms: int
    songs: tuple[SongView, ...]


@dataclass(frozen=True, slots=True)
class Changed:
    reason: str | None
    view: View | None = None


@dataclass(frozen=True, slots=True)
class _Draft:
    revision_id: UUID
    fingerprint: str
    map_version: int | None
    crossfade_ms: int
    songs: tuple[SongView, ...]  # without corrections


def _music(raw: object) -> SpotifyRevisionMusic | None:
    if not isinstance(raw, dict):
        return None
    try:
        return SpotifyRevisionMusic.model_validate_json(json.dumps(raw))
    except ValidationError:
        return None


def _ids(ctx: UserContext, class_id: UUID, **more: Any) -> dict[str, Any]:
    return {"owner_id": ctx.user_id, "class_id": class_id, **more}


async def _draft(db: AsyncSession, ctx: UserContext, class_id: UUID) -> _Draft | str:
    """The class's current draft with its songs and their facts, or ``not_found`` / ``no_draft``."""
    row = (await db.execute(_CURRENT, _ids(ctx, class_id))).first()
    if row is None:
        return "not_found"
    music = _music(row.music) if row.kind == "draft" else None
    if row.current is None or music is None or music.song_list_id is None:
        return "no_draft"
    stored = await song_list.load(db, ctx.user_id, class_id, music.song_list_id)
    if stored is None:
        return "no_draft"
    facts = model.facts_of(stored.provenance)
    revision: dict[str, Any] = row.provenance if isinstance(row.provenance, dict) else {}  # pyright: ignore[reportUnknownVariableType]
    songs = tuple(
        SongView(i, i + 1, s.title, s.artist, s.length_sec * 1000, facts.get(i), None)
        for i, s in enumerate(stored.content.songs)
    )
    crossfade = model.crossfade_ms_of(revision, stored.content.crossfade_sec)
    return _Draft(row.current, music.fingerprint, model.map_version_of(revision), crossfade, songs)


async def _view(db: AsyncSession, ctx: UserContext, class_id: UUID, draft: _Draft) -> View:
    rows = (await db.execute(_ROWS, _ids(ctx, class_id, fingerprint=draft.fingerprint))).all()
    mine = {int(r.song): r for r in rows}
    songs: list[SongView] = []
    for s in draft.songs:
        found = mine.get(s.song)
        if found is None:
            songs.append(s)
            continue
        stored = model.stored_set_against(found.set_against_analysis)
        now = model.set_against(s.fact, draft.map_version)
        correction = CorrectionView(int(found.offset_ms), stored, model.state_of(stored, now))
        songs.append(SongView(s.piece, s.song, s.title, s.artist, s.length_ms, s.fact, correction))
    return View(draft.revision_id, draft.map_version, draft.crossfade_ms, tuple(songs))


async def view(factory: Factory, ctx: UserContext, class_id: UUID) -> View | str:
    """The draft's songs with facts and corrections, or ``not_found`` / ``no_draft``."""
    require_context(ctx)
    async with user_session(factory, ctx) as db:
        draft = await _draft(db, ctx, class_id)
        return draft if isinstance(draft, str) else await _view(db, ctx, class_id, draft)


async def _writable(
    db: AsyncSession, ctx: UserContext, class_id: UUID, revision_id: UUID, song: int
) -> _Draft | str:
    """The draft a write applies to, after the checks every write makes (module docstring)."""
    locked = (await db.execute(_LOCK, _ids(ctx, class_id))).first()
    if locked is None:
        return "not_found"
    own = await db.execute(_REVISION, _ids(ctx, class_id, revision_id=revision_id))
    if own.first() is None:  # not one of this class's own revisions: as an unknown id
        return "not_found"
    if locked.current != revision_id:
        return "stale_base"
    not_ready = await revision_ready(db, ctx.user_id, class_id, revision_id)
    if not_ready is not None:
        return not_ready
    draft = await _draft(db, ctx, class_id)
    if isinstance(draft, str):
        return draft
    if not 1 <= song <= len(draft.songs):
        return "invalid_input"
    return draft


async def set_offset(
    factory: Factory, ctx: UserContext, class_id: UUID, revision_id: UUID, song: int, offset_ms: int
) -> Changed:
    """Set song ``song``'s correction (1-based) to ``offset_ms``, against the analysis now.
    Idempotent: setting it again confirms it again."""
    require_context(ctx)
    if not model.valid_offset(offset_ms):
        return Changed("invalid_input")
    async with user_session(factory, ctx) as db:
        draft = await _writable(db, ctx, class_id, revision_id, song)
        if isinstance(draft, str):
            return Changed(draft)
        against = model.set_against(draft.songs[song - 1].fact, draft.map_version)
        if against is None:  # no analysed file: nothing to set a correction against
            return Changed("no_analysis")
        params = _ids(ctx, class_id, fingerprint=draft.fingerprint, song=song)
        exists = (await db.execute(_EXISTS, params)).first() is not None
        count = (await db.execute(_COUNT, params)).scalar_one()
        if not exists and count >= MAX_ROWS:
            return Changed("correction_limit")
        await db.execute(
            _UPSERT, {**params, "offset_ms": offset_ms, "against": json.dumps(against.stored())}
        )
        return Changed(None, await _view(db, ctx, class_id, draft))


async def clear_offset(
    factory: Factory, ctx: UserContext, class_id: UUID, revision_id: UUID, song: int
) -> Changed:
    """Clear song ``song``'s correction. Idempotent."""
    require_context(ctx)
    async with user_session(factory, ctx) as db:
        draft = await _writable(db, ctx, class_id, revision_id, song)
        if isinstance(draft, str):
            return Changed(draft)
        await db.execute(_DELETE, _ids(ctx, class_id, fingerprint=draft.fingerprint, song=song))
        return Changed(None, await _view(db, ctx, class_id, draft))
