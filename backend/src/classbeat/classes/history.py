"""The revision history of a class: the timeline and the comparison of two revisions (ADR 0007,
amendment of 2026-11-03, part H; issue #545).

``GET /api/classes/{id}/history`` lists every ``draft`` revision of the user's own class with what
the timeline shows: what asked for it (the first design, the user's own chat request, or the class
it was duplicated from), when, its length and TSS, and whether it is the current revision, the one
saved in MOWL and one designed on approved songs. ``GET /api/classes/{id}/history/compare`` is the
change between two of them: ``classbeat.design.diff.summarize`` unchanged, plus warning counts and
whether the music is the same, as booleans only.

Read only, one ``user_session`` per call, every statement scoped by ``owner_id`` and ``class_id``.
Another user's id, an unknown one, a malformed one and a revision that is not a draft of this class
are the same 404 ``not_found``. A stored content that no longer validates is 500 ``internal`` with
the reason only. No MOWL or Spotify id, link id, fingerprint or run id leaves this module: the
lengths and TSS are computed here from the stored content with ``mowl.workout`` (never a model
number), and the music comparison answers with booleans.
"""

import re
from datetime import datetime
from typing import Annotated, Any, Final, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse
from mowl import workout
from pydantic import BaseModel, ValidationError
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from classbeat import auth
from classbeat.agent.design import ZONE7
from classbeat.agent.schemas import stored_draft
from classbeat.classes.service import stored_brief
from classbeat.context import UserContext, require_context
from classbeat.db import user_session
from classbeat.design import diff

Factory = async_sessionmaker[AsyncSession]

router = APIRouter(prefix="/api/classes", tags=["classes"])

HISTORY_LIMIT: Final = 200  # the most revisions a class can hold (CLASSBEAT_REVISIONS_PER_CLASS)
_UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
# The ``uuid`` text check in SQL, so a malformed id in a stored provenance never fails the read.
_UUID_SQL: Final = "'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'"

_CLASS = text(
    "SELECT (brief IS NOT NULL AND music_source IS NOT NULL) AS can_change FROM classes "
    "WHERE owner_id = :owner_id AND id = :class_id"
)

_ROWS = text(f"""
SELECT r.id, r.number, r.created_at, r.parent_id, p.number AS parent_number, r.content,
    r.content_hash, r.music, r.provenance,
    coalesce(r.provenance ->> 'origin', 'design_draft') AS origin,
    (r.id = c.current_revision_id) AS is_current,
    m.body AS request,
    EXISTS (SELECT 1 FROM saved_workouts s WHERE s.owner_id = r.owner_id
        AND s.class_id = r.class_id AND s.revision_id = r.id AND s.detached_at IS NULL)
        AS saved_live,
    EXISTS (SELECT 1 FROM saved_workouts s WHERE s.owner_id = r.owner_id
        AND s.class_id = r.class_id AND s.revision_id = r.id AND s.detached_at IS NOT NULL)
        AS saved_detached,
    src.id AS source_class_id, src.title AS source_title
FROM class_revisions r
JOIN classes c ON c.owner_id = r.owner_id AND c.id = r.class_id
LEFT JOIN class_revisions p ON p.owner_id = r.owner_id AND p.id = r.parent_id
LEFT JOIN class_messages m ON m.owner_id = r.owner_id AND m.class_id = r.class_id
    AND m.role = 'user'
    AND (r.provenance ->> 'request_message_id') ~ {_UUID_SQL}
    AND m.id = CAST(r.provenance ->> 'request_message_id' AS uuid)
LEFT JOIN classes src ON src.owner_id = r.owner_id
    AND (r.provenance -> 'duplicated_from' ->> 'class_id') ~ {_UUID_SQL}
    AND src.id = CAST(r.provenance -> 'duplicated_from' ->> 'class_id' AS uuid)
WHERE r.owner_id = :owner_id AND r.class_id = :class_id AND r.kind = 'draft'
    AND (CAST(:ids AS uuid[]) IS NULL OR r.id = ANY(CAST(:ids AS uuid[])))
ORDER BY r.number
LIMIT :limit""")  # noqa: S608 - constants only


class Unreadable(Exception):
    """A stored revision that no longer validates: answered 500 ``internal``, nothing shown."""


class SummaryOut(BaseModel):
    segments: int
    intervals: int
    total_sec: int


class DuplicatedFromOut(BaseModel):
    """The class a duplicate was copied from; ``class_id`` and ``class_title`` are ``None``
    when that class is gone (or the stored id is not one of the user's classes)."""

    class_id: UUID | None
    class_title: str | None
    number: int | None


class TimelineEntryOut(BaseModel):
    id: UUID
    number: int
    parent_id: UUID | None
    parent_number: int | None
    origin: str
    created_at: datetime
    is_current: bool
    content_hash: str
    summary: SummaryOut
    tss: float | None
    tss_approximate: bool
    request: str | None
    saved: Literal["live", "detached", "none"]
    from_songs: bool
    duplicated_from: DuplicatedFromOut | None


class HistoryOut(BaseModel):
    sport: Literal["cycling", "running"]
    # The class has a stored brief and its own music source, which Change it needs: a duplicate of
    # a class on picked songs has none (ADR 0007 amendment of 2026-11-03, part D item 5).
    can_change: bool
    revisions: list[TimelineEntryOut]


class WarningsOut(BaseModel):
    unknown_bpm: int
    unsure_beat_one: int


class SegmentsChangeOut(BaseModel):
    added: int
    removed: int


class IntervalsChangeOut(BaseModel):
    added: int
    removed: int
    changed: int


class BeforeAfterOut(BaseModel):
    before: float
    after: float


class MusicChangeOut(BaseModel):
    same_source: bool
    same_fingerprint: bool


class CompareOut(BaseModel):
    from_id: UUID
    from_number: int
    to_id: UUID
    to_number: int
    segments: SegmentsChangeOut
    intervals: IntervalsChangeOut
    duration_sec: BeforeAfterOut
    tss: BeforeAfterOut
    text_changed: bool
    warnings: dict[Literal["from", "to"], WarningsOut]
    music: MusicChangeOut


def _sport(brief: Any) -> Literal["cycling", "running"]:
    return "running" if brief is not None and brief.sport == "running" else "cycling"


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}  # pyright: ignore[reportUnknownVariableType]


def _spec(content: Any, sport: str) -> dict[str, Any]:
    try:
        return stored_draft(content, sport).to_spec()
    except (ValidationError, TypeError, ValueError) as exc:
        raise Unreadable from exc


def _tss(spec: dict[str, Any]) -> float | None:
    try:
        return round(float(workout.tss(spec)), 1)
    except (KeyError, TypeError, ValueError, ZeroDivisionError):
        return None


def _approximate(provenance: dict[str, Any], spec: dict[str, Any]) -> bool:
    stored = provenance.get("tss_approximate")  # absent before ADR 0012 section 7
    if isinstance(stored, bool):
        return stored
    return any(i.get("zone") == ZONE7 for s in spec["segments"] for i in s["intervals"])


def _duplicated_from(row: Any) -> DuplicatedFromOut | None:
    if row.origin != "duplicate":
        return None
    raw = _dict(_dict(row.provenance).get("duplicated_from"))
    number = raw.get("number")
    return DuplicatedFromOut(
        class_id=row.source_class_id,
        class_title=row.source_title,
        number=number if isinstance(number, int) and not isinstance(number, bool) else None,
    )


def entry(row: Any, sport: str) -> TimelineEntryOut:
    """One timeline entry from a row of ``_ROWS`` (``Unreadable`` when its content no longer
    validates under the class's sport)."""
    spec = _spec(row.content, sport)
    intervals = [i for s in spec["segments"] for i in s["intervals"]]
    music = _dict(row.music)
    saved: Literal["live", "detached", "none"] = (
        "live" if row.saved_live else "detached" if row.saved_detached else "none"
    )
    return TimelineEntryOut(
        id=row.id,
        number=row.number,
        parent_id=row.parent_id,
        parent_number=row.parent_number,
        origin=row.origin,
        created_at=row.created_at,
        is_current=bool(row.is_current),
        content_hash=bytes(row.content_hash).hex(),
        summary=SummaryOut(
            segments=len(spec["segments"]),
            intervals=len(intervals),
            total_sec=int(workout.total_seconds(spec)),
        ),
        tss=_tss(spec),
        tss_approximate=_approximate(_dict(row.provenance), spec),
        request=row.request if row.origin == "revise_draft" else None,
        saved=saved,
        from_songs=music.get("song_list_id") is not None,
        duplicated_from=_duplicated_from(row),
    )


def _params(ctx: UserContext, class_id: UUID, ids: list[UUID] | None = None) -> dict[str, Any]:
    return {
        "owner_id": ctx.user_id,
        "class_id": class_id,
        "ids": None if ids is None else [str(i) for i in ids],
        "limit": HISTORY_LIMIT,
    }


async def history(factory: Factory, ctx: UserContext, class_id: UUID) -> HistoryOut | None:
    """The timeline of the user's own class, oldest first; ``None`` when it is not theirs."""
    require_context(ctx)
    async with user_session(factory, ctx) as db:
        found = (await db.execute(_CLASS, _params(ctx, class_id))).first()
        if found is None:
            return None
        sport = _sport(await stored_brief(db, ctx.user_id, class_id))
        rows = (await db.execute(_ROWS, _params(ctx, class_id))).all()
    return HistoryOut(
        sport=sport, can_change=bool(found.can_change), revisions=[entry(r, sport) for r in rows]
    )


def _warnings(music: dict[str, Any]) -> WarningsOut:
    unknown = music.get("unknown_bpm")
    unsure = music.get("unsure_beat_one")
    return WarningsOut(
        unknown_bpm=len(unknown) if isinstance(unknown, list) else 0,  # pyright: ignore[reportUnknownArgumentType]
        unsure_beat_one=len(unsure) if isinstance(unsure, list) else 0,  # pyright: ignore[reportUnknownArgumentType]
    )


def _source_key(music: dict[str, Any]) -> tuple[Any, ...]:
    """What names the music a revision was fitted to, compared but never returned."""
    source = music.get("source", "mowl_spotify_playlist")  # absent on the earliest revisions
    return (source, music.get("link_id"), music.get("mix_id"))


def compare_rows(older: Any, newer: Any, sport: str) -> CompareOut:
    _spec(older.content, sport)  # Unreadable before anything is computed
    _spec(newer.content, sport)
    change = diff.summarize(older.content, newer.content, sport)
    a, b = _dict(older.music), _dict(newer.music)
    return CompareOut(
        from_id=older.id,
        from_number=older.number,
        to_id=newer.id,
        to_number=newer.number,
        segments=SegmentsChangeOut(**change["segments"]),
        intervals=IntervalsChangeOut(**change["intervals"]),
        duration_sec=BeforeAfterOut(**change["duration_sec"]),
        tss=BeforeAfterOut(**change["tss"]),
        text_changed=bool(change["text_changed"]),
        warnings={"from": _warnings(a), "to": _warnings(b)},
        music=MusicChangeOut(
            same_source=_source_key(a) == _source_key(b),
            same_fingerprint=a.get("fingerprint") is not None
            and a.get("fingerprint") == b.get("fingerprint"),
        ),
    )


async def compare(
    factory: Factory, ctx: UserContext, class_id: UUID, from_id: UUID, to_id: UUID
) -> CompareOut | None:
    """The change from ``from_id`` to ``to_id``, both drafts of the user's own class; ``None``
    when either is not."""
    require_context(ctx)
    async with user_session(factory, ctx) as db:
        sport = _sport(await stored_brief(db, ctx.user_id, class_id))
        rows = (await db.execute(_ROWS, _params(ctx, class_id, [from_id, to_id]))).all()
    by_id = {r.id: r for r in rows}
    if from_id not in by_id or to_id not in by_id:
        return None
    return compare_rows(by_id[from_id], by_id[to_id], sport)


def _refuse(reason: str, status: int) -> JSONResponse:
    return JSONResponse({"detail": {"reason": reason}}, status_code=status)


def _uuid(raw: str) -> UUID | None:
    return UUID(raw) if _UUID.fullmatch(raw.lower()) else None


@router.get("/{class_id}/history", response_model=HistoryOut, responses={404: {}, 500: {}})
async def get_history(
    class_id: str, request: Request, ctx: Annotated[UserContext, Depends(auth.require_user)]
) -> HistoryOut | JSONResponse:
    wanted = _uuid(class_id)
    if wanted is None:
        return _refuse("not_found", 404)
    try:
        found = await history(request.app.state.session_factory, ctx, wanted)
    except Unreadable:
        return _refuse("internal", 500)
    return _refuse("not_found", 404) if found is None else found


@router.get("/{class_id}/history/compare", response_model=CompareOut, responses={404: {}, 500: {}})
async def get_compare(
    class_id: str,
    request: Request,
    ctx: Annotated[UserContext, Depends(auth.require_user)],
    from_: Annotated[str, Query(alias="from", max_length=64)],
    to: Annotated[str, Query(max_length=64)],
) -> CompareOut | JSONResponse:
    wanted, older, newer = _uuid(class_id), _uuid(from_), _uuid(to)
    if wanted is None or older is None or newer is None:
        return _refuse("not_found", 404)
    try:
        found = await compare(request.app.state.session_factory, ctx, wanted, older, newer)
    except Unreadable:
        return _refuse("internal", 500)
    return _refuse("not_found", 404) if found is None else found
