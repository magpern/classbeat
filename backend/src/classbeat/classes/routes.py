"""``/api/classes``: my classes, their revisions and conversation, the pick of the current revision
and the class delete (ADR 0007 sections 2, 11 and 12; issue C2).

Signed in; the ``POST`` and ``DELETE`` are CSRF-checked by the middleware. Another user's id, an
unknown id and a malformed one are the same 404 ``not_found``. Errors carry a reason code only.
409 reasons: ``class_busy``, ``hash_mismatch`` (the pick) and ``call_may_be_running``,
``uncertain_write`` (the delete).
"""

import base64
import json
import re
from dataclasses import asdict
from datetime import datetime
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from classbeat import auth
from classbeat.agent.design import ZONE7
from classbeat.agent.schemas import WorkoutDraft
from classbeat.classes import service, song_views
from classbeat.classes.schemas import (
    ClassDetailOut,
    ClassesOut,
    ClassOut,
    DeletedOut,
    MessageOut,
    MessagesOut,
    MosaicSongOut,
    MusicPlaylistOut,
    MusicPlaylistsOut,
    PickIn,
    PlaylistNameOut,
    PlaylistOut,
    ProvenanceOut,
    RevisionOut,
    RevisionsOut,
    RevisionSummaryOut,
    SongListOut,
    SongOut,
    SongsOut,
    UnknownBpmOut,
)
from classbeat.context import UserContext
from classbeat.covers import cover_url

router = APIRouter(prefix="/api/classes", tags=["classes"])
music_router = APIRouter(prefix="/api/music", tags=["music"])

HASH = re.compile(r"^[0-9a-f]{64}$")
KEPT_NOTE = "The workouts classbeat saved to your MOWL account stay there; delete them in MOWL."
SPOTIFY_NOTE = (
    "The playlists classbeat made in your Spotify account stay there; delete them in Spotify."
)


def _refuse(reason: str, status: int = 409) -> JSONResponse:
    return JSONResponse({"detail": {"reason": reason}}, status_code=status)


def _not_found() -> JSONResponse:
    return _refuse("not_found", 404)


def _uuid(raw: str) -> UUID | None:
    try:
        return UUID(raw)
    except ValueError:
        return None


def _encode_cursor(after: service.Cursor) -> str:
    raw = json.dumps([after[0].isoformat(), str(after[1])]).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _decode_cursor(cursor: str) -> service.Cursor | None:
    try:
        at, class_id = json.loads(base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)))
        when = datetime.fromisoformat(at)
        parsed = UUID(class_id)
    except (ValueError, TypeError):  # bad base64, JSON, shape, timestamp or uuid
        return None
    # A cursor names an instant: a naive timestamp would be read in the session's time zone.
    if when.utcoffset() is None:
        return None
    return when, parsed


@router.get("", response_model=ClassesOut, responses={422: {}})
async def list_classes(
    request: Request,
    ctx: Annotated[UserContext, Depends(auth.require_user)],
    limit: Annotated[int, Query(ge=1, le=service.LIST_LIMIT)] = service.LIST_LIMIT,
    cursor: Annotated[str | None, Query(max_length=200)] = None,
) -> ClassesOut | JSONResponse:
    """Newest first, at most ``limit`` (default and maximum 500) per page; ``next_cursor`` goes
    back as ``cursor`` for the next one."""
    after = None if cursor is None else _decode_cursor(cursor)
    if cursor is not None and after is None:
        return _refuse("invalid_cursor", 422)
    page = await service.list_classes(
        request.app.state.session_factory, ctx, limit=limit, after=after
    )
    return ClassesOut(
        classes=[ClassOut.of(r) for r in page.rows],
        next_cursor=None if page.next_after is None else _encode_cursor(page.next_after),
    )


@music_router.get("/class-playlists", response_model=MusicPlaylistsOut)
async def class_playlists(
    request: Request, ctx: Annotated[UserContext, Depends(auth.require_user)]
) -> MusicPlaylistsOut:
    """The playlists classbeat made for the signed-in user, newest first (the Music page, ADR 0013
    sections 4 and 9). A read only; another user's playlists are never in it. A link is the
    owner's own, built here from a verified playlist's stored id; covers pass the pinned-host
    check again just before they are returned."""
    found = await song_views.music_playlists(request.app.state.session_factory, ctx)
    return MusicPlaylistsOut(
        playlists=[
            MusicPlaylistOut(
                class_id=p.class_id,
                class_title=p.class_title,
                name=p.name,
                link=p.link,
                detached=p.detached,
                note=p.note,
                songs=[MosaicSongOut(title=s.title, cover=cover_url(s.cover)) for s in p.songs],
            )
            for p in found
        ]
    )


@router.get("/{class_id}", response_model=ClassDetailOut, responses={404: {}})
async def get_class(
    class_id: str, request: Request, ctx: Annotated[UserContext, Depends(auth.require_user)]
) -> ClassDetailOut | JSONResponse:
    wanted = _uuid(class_id)
    found = (
        None
        if wanted is None
        else await service.get_class_detail(request.app.state.session_factory, ctx, wanted)
    )
    if found is None:
        return _not_found()
    playlists = found.playlists
    return ClassDetailOut(
        **ClassOut.of(found.row).model_dump(),
        songs=SongsOut(**asdict(found.songs)),
        playlist=None if playlists.current is None else PlaylistOut(**asdict(playlists.current)),
        spotify_playlists=[PlaylistOut(**asdict(p)) for p in playlists.all],
        brief=found.brief,
    )


@router.get("/{class_id}/revisions", response_model=RevisionsOut, responses={404: {}})
async def list_revisions(
    class_id: str, request: Request, ctx: Annotated[UserContext, Depends(auth.require_user)]
) -> RevisionsOut | JSONResponse:
    wanted = _uuid(class_id)
    rows = (
        None
        if wanted is None
        else await service.list_revisions(request.app.state.session_factory, ctx, wanted)
    )
    if rows is None:
        return _not_found()
    return RevisionsOut(revisions=[RevisionSummaryOut.of(r) for r in rows])


@router.get(
    "/{class_id}/revisions/{revision_id}",
    response_model=RevisionOut | SongListOut,
    responses={404: {}},
)
async def get_revision(
    class_id: str,
    revision_id: str,
    request: Request,
    ctx: Annotated[UserContext, Depends(auth.require_user)],
) -> RevisionOut | SongListOut | JSONResponse:
    wanted_class, wanted = _uuid(class_id), _uuid(revision_id)
    if wanted_class is None or wanted is None:
        return _not_found()
    factory = request.app.state.session_factory
    row = await service.get_revision(factory, ctx, wanted_class, wanted)
    if row is None:
        return await _song_list(factory, ctx, wanted_class, wanted)
    try:
        draft = WorkoutDraft.model_validate(row.content)  # only keys the schema names are shown
        raw: Any = row.music.get("unknown_bpm", [])
        unknown = [UnknownBpmOut(index=int(u["index"]), title=str(u["title"])) for u in raw]
        provenance = ProvenanceOut.model_validate(row.provenance)
        summary = RevisionSummaryOut.of(row)
        stored = row.provenance.get("tss_approximate")  # absent before ADR 0012 section 7
        approximate = (
            stored
            if isinstance(stored, bool)
            else any(i.zone == ZONE7 for s in draft.segments for i in s.intervals)
        )
    except (ValidationError, KeyError, TypeError, ValueError):
        return JSONResponse({"detail": {"reason": "internal"}}, status_code=500)
    return RevisionOut(
        **summary.model_dump(),
        kind=row.kind,
        content=draft.model_dump(mode="json", exclude_none=True),
        unknown_bpm=unknown,
        provenance=provenance,
        tss_approximate=approximate,
    )


async def _song_list(
    factory: Any, ctx: UserContext, class_id: UUID, revision_id: UUID
) -> SongListOut | JSONResponse:
    """A ``song_list`` revision: songs without ids, totals, ``new`` marks, a plain-text note."""
    view = await song_views.get_song_list(factory, ctx, class_id, revision_id)
    if view is None:
        return _not_found()
    if view == "unreadable":  # the stored hash or content no longer checks: never shown
        return JSONResponse({"detail": {"reason": "internal"}}, status_code=500)
    c = view.content
    return SongListOut(
        id=view.id,
        number=view.number,
        created_at=view.created_at,
        parent_id=view.parent_id,
        parent_number=view.parent_number,
        status=view.status,
        job_id=view.job_id,
        expires_at=view.expires_at,
        approved_at=view.approved_at,
        content_hash=view.content_hash,
        mode=c.mode,
        target_sec=c.target_sec,
        total_sec=c.total_sec,
        crossfade_sec=c.crossfade_sec,
        songs=[
            SongOut(
                position=i,
                title=song_views.plain(s.title),
                artist=song_views.plain(s.artist),
                role=s.role,
                bpm=s.bpm,
                length_sec=s.length_sec,
                new=s.new,
                cover=cover_url(s.cover),  # the pinned host only, again (ADR 0013 section 9)
            )
            for i, s in enumerate(c.songs, start=1)
        ],
        omitted_no_bpm=c.omitted_no_bpm,
        note=song_views.plain(c.note),
    )


@router.get("/{class_id}/messages", response_model=MessagesOut, responses={404: {}})
async def list_messages(
    class_id: str, request: Request, ctx: Annotated[UserContext, Depends(auth.require_user)]
) -> MessagesOut | JSONResponse:
    wanted = _uuid(class_id)
    rows = (
        None
        if wanted is None
        else await service.list_messages(request.app.state.session_factory, ctx, wanted)
    )
    if rows is None:
        return _not_found()
    return MessagesOut(
        messages=[
            MessageOut(
                seq=r.seq,
                role=r.role,
                body=r.body,
                job_id=r.job_id,
                base_revision_id=r.base_revision_id,
                result_revision_id=r.result_revision_id,
                change=r.change,
                created_at=r.created_at,
            )
            for r in rows
        ]
    )


@router.post("/{class_id}/current", response_model=ClassOut, responses={404: {}, 409: {}, 422: {}})
async def pick_current(
    class_id: str,
    body: PickIn,
    request: Request,
    ctx: Annotated[UserContext, Depends(auth.require_user)],
) -> ClassOut | JSONResponse:
    wanted = _uuid(class_id)
    if wanted is None:
        return _not_found()
    if not HASH.fullmatch(body.content_hash):
        return _refuse("invalid_input", 422)
    factory = request.app.state.session_factory
    done = await service.pick(
        factory, ctx, wanted, body.revision_id, bytes.fromhex(body.content_hash)
    )
    if done.reason == "not_found":
        return _not_found()
    if done.reason is not None:
        return _refuse(done.reason)
    row = await service.get_class(factory, ctx, wanted)
    return _not_found() if row is None else ClassOut.of(row)


@router.delete("/{class_id}", response_model=DeletedOut, responses={404: {}, 409: {}})
async def delete_class(
    class_id: str,
    request: Request,
    ctx: Annotated[UserContext, Depends(auth.require_user)],
    confirm_uncertain: bool = False,
) -> DeletedOut | JSONResponse:
    wanted = _uuid(class_id)
    if wanted is None:
        return _not_found()
    done = await service.delete(
        request.app.state.session_factory, ctx, wanted, confirm_uncertain=confirm_uncertain
    )
    if done.reason == "not_found":
        return _not_found()
    if done.reason is not None:
        return _refuse(done.reason)
    left = [PlaylistNameOut(name=p.name, detached=p.detached) for p in done.playlists]
    return DeletedOut(
        deleted=True,
        mowl_workouts_kept=done.kept,
        note=KEPT_NOTE,
        spotify_playlists_left=left,
        spotify_note=SPOTIFY_NOTE if left else None,
    )
