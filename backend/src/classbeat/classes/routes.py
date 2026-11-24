"""``/api/classes``: my classes, their revisions and conversation and the class delete (ADR 0007
sections 2, 11 and 12; issue C2). The pick of the current revision and the user's edits are
``classes.edit_routes``.

Signed in; the ``POST`` and ``DELETE`` are CSRF-checked by the middleware. Another user's id, an
unknown id and a malformed one are the same 404 ``not_found``; the user's own class that a failed
first creation discarded is 404 ``class_discarded`` with that job's id (ADR 0039). Errors carry a
reason code only.
409 reasons: ``limit_exceeded`` (the 33rd dismissed rule, ADR 0017), ``class_busy``,
``stale_match`` (the Swedebeat selection) and ``call_may_be_running``, ``uncertain_write`` (the
delete).

A ``swedebeat_match`` revision (ADR 0016 section 6) is read only while the effective
``swedebeat_enabled`` flag is on; off, its id answers 404 ``feature_off`` without its content being
read, and the class detail carries no match pointer.
"""

from dataclasses import asdict
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from classbeat import auth
from classbeat.agent.design import ZONE7
from classbeat.agent.stored_draft import stored_draft
from classbeat.classes import (
    beat_one,
    pattern_notes,
    removal,
    selection,
    service,
    song_list_out,
    song_views,
)
from classbeat.classes.answers import internal as _internal
from classbeat.classes.answers import not_found as _not_found
from classbeat.classes.answers import refuse as _refuse
from classbeat.classes.answers import uuid_of as _uuid
from classbeat.classes.discarded import not_found_answer
from classbeat.classes.list_cursor import decode_cursor, encode_cursor
from classbeat.classes.music_playlists_out import MosaicSongOut, MusicPlaylistOut, MusicPlaylistsOut
from classbeat.classes.provenance_out import ProvenanceOut
from classbeat.classes.schemas import (
    BeatOneOut,
    ClassDetailOut,
    ClassesOut,
    ClassOut,
    DismissedRulesOut,
    MessageOut,
    MessagesOut,
    PlayerStateOut,
    PlaylistOut,
    RevisionOut,
    RevisionsOut,
    RevisionSummaryOut,
    RuleHitOut,
    SongsOut,
    SwedebeatMatchPointerOut,
    UnknownBpmOut,
)
from classbeat.classes.song_list_out import SongListOut, SwedebeatMatchOut
from classbeat.connections.swedebeat_account import swedebeat_enabled
from classbeat.context import UserContext
from classbeat.covers import cover_url

router = APIRouter(prefix="/api/classes", tags=["classes"])
music_router = APIRouter(prefix="/api/music", tags=["music"])


@router.get("", response_model=ClassesOut, responses={422: {}})
async def list_classes(
    request: Request,
    ctx: Annotated[UserContext, Depends(auth.require_user)],
    limit: Annotated[int, Query(ge=1, le=service.LIST_LIMIT)] = service.LIST_LIMIT,
    cursor: Annotated[str | None, Query(max_length=200)] = None,
) -> ClassesOut | JSONResponse:
    """Newest first, at most ``limit`` (default and maximum 500) per page; ``next_cursor`` goes
    back as ``cursor`` for the next one."""
    after = None if cursor is None else decode_cursor(cursor)
    if cursor is not None and after is None:
        return _refuse("invalid_cursor", 422)
    page = await service.list_classes(
        request.app.state.session_factory, ctx, limit=limit, after=after
    )
    return ClassesOut(
        classes=[ClassOut.of(r) for r in page.rows],
        next_cursor=None if page.next_after is None else encode_cursor(page.next_after),
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
    if wanted is None:
        return _not_found()
    factory = request.app.state.session_factory
    enabled = await swedebeat_enabled(request.app.state.settings, factory)
    found = await service.get_class_detail(factory, ctx, wanted, with_match=enabled)
    if found is None:  # a discarded class says so, with its ended job (ADR 0039)
        return await not_found_answer(factory, ctx, wanted)
    beats = await beat_one.view(factory, ctx, wanted)
    playlists = found.playlists
    match = found.match
    detail = ClassDetailOut(
        **ClassOut.of(found.row).model_dump(),
        songs=SongsOut(**asdict(found.songs)),
        playlist=None if playlists.current is None else PlaylistOut(**asdict(playlists.current)),
        spotify_playlists=[PlaylistOut(**asdict(p)) for p in playlists.all],
        brief=found.brief,
        player=None if found.player is None else PlayerStateOut(**asdict(found.player)),
        swedebeat_match=None
        if match is None
        else SwedebeatMatchPointerOut(revision_id=match.revision_id, approved=match.approved),
        beat_one=BeatOneOut(available=False) if beats is None else BeatOneOut.of(beats),
        **found.fields(),
    )
    if enabled:
        return detail
    # Flag off: the pointer is absent, not null (no match row was read). Built by hand, so
    # test_flag_off_class_detail_is_the_flag_on_shape_without_the_pointer pins it to the model.
    return JSONResponse(detail.model_dump(mode="json", exclude={"swedebeat_match"}))


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
    response_model=RevisionOut | SongListOut | SwedebeatMatchOut,
    responses={404: {}, 500: {}},
)
async def get_revision(
    class_id: str,
    revision_id: str,
    request: Request,
    ctx: Annotated[UserContext, Depends(auth.require_user)],
) -> RevisionOut | SongListOut | SwedebeatMatchOut | JSONResponse:
    """A ``draft``, a ``song_list`` or (flag on) a ``swedebeat_match`` of the user's own class."""
    wanted_class, wanted = _uuid(class_id), _uuid(revision_id)
    if wanted_class is None or wanted is None:
        return _not_found()
    factory = request.app.state.session_factory
    row = await service.get_revision(factory, ctx, wanted_class, wanted)
    if row is None:
        return await song_list_out.song_list(request, factory, ctx, wanted_class, wanted)
    brief, dismissed = await service.get_pattern_inputs(factory, ctx, wanted_class)
    try:
        # Only keys the class's sport's schema names are shown (ADR 0018: absent = cycling).
        draft = stored_draft(row.content, brief.sport if brief is not None else None)
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
        content = draft.model_dump(mode="json", exclude_none=True)
        notes = pattern_notes.pattern_notes(content, brief, dismissed, row.provenance)
        hits = [RuleHitOut.model_validate(h) for h in notes.hits]
    except (ValidationError, KeyError, TypeError, ValueError):
        return _internal()
    return RevisionOut(
        **summary.model_dump(),
        kind=row.kind,
        content=content,
        unknown_bpm=unknown,
        provenance=provenance,
        tss_approximate=approximate,
        rule_hits=hits,
        notes_use_current_rules=notes.uses_current_rules,
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


@router.post(
    "/{class_id}/swedebeat-match",
    response_model=selection.SelectionOut,
    responses={404: {}, 409: {}, 422: {}},
)
async def approve_swedebeat_match(
    class_id: str,
    request: Request,
    ctx: Annotated[UserContext, Depends(auth.require_user)],
) -> selection.SelectionOut | JSONResponse:
    """Record the approved ``swedebeat_match`` of this class (ADR 0016 section 6).

    The flag is answered before the body is read and before the class is. The response is the
    revision id and its content hash: no title, artist or catalogue id.
    """
    if not await swedebeat_enabled(request.app.state.settings, request.app.state.session_factory):
        return _refuse("feature_off", 404)
    wanted = _uuid(class_id)
    if wanted is None:
        return _not_found()
    try:
        body = selection.SelectionIn.model_validate_json(await request.body())
    except ValidationError:
        return _refuse("invalid_input", 422)
    done = await selection.record(request.app.state.session_factory, ctx, wanted, body.revision_id)
    if done.approved is None:
        return _not_found() if done.reason == "not_found" else _refuse(done.reason or "stale_match")
    return selection.SelectionOut(
        revision_id=done.approved.revision_id, content_hash=done.approved.content_hash
    )


@router.delete("/{class_id}", response_model=removal.DeletedOut, responses={404: {}, 409: {}})
async def delete_class(
    class_id: str,
    request: Request,
    ctx: Annotated[UserContext, Depends(auth.require_user)],
    confirm_uncertain: bool = False,
) -> removal.DeletedOut | JSONResponse:
    wanted = _uuid(class_id)
    if wanted is None:
        return _not_found()
    done = await removal.delete(
        request.app.state.session_factory, ctx, wanted, confirm_uncertain=confirm_uncertain
    )
    if done.reason == "not_found":
        return _not_found()
    if done.reason is not None:
        return _refuse(done.reason)
    return removal.deleted_out(done)


async def _dismissal(
    class_id: str, rule_id: str, request: Request, ctx: UserContext, *, dismissed: bool
) -> DismissedRulesOut | JSONResponse:
    wanted = _uuid(class_id)
    if wanted is None:
        return _not_found()
    done = await service.set_dismissed(
        request.app.state.session_factory, ctx, wanted, rule_id, dismissed=dismissed
    )
    if done.reason == "not_found":
        return _not_found()
    if done.reason == "invalid_input":
        return _refuse("invalid_input", 422)
    if done.reason is not None:
        return _refuse(done.reason)
    return DismissedRulesOut(dismissed_rules=list(done.rules))


@router.put(
    "/{class_id}/dismissed-rules/{rule_id}",
    response_model=DismissedRulesOut,
    responses={404: {}, 409: {}, 422: {}},
)
async def dismiss_rule(
    class_id: str,
    rule_id: str,
    request: Request,
    ctx: Annotated[UserContext, Depends(auth.require_user)],
) -> DismissedRulesOut | JSONResponse:
    """Dismiss a soft rule for this class (ADR 0017 section 4). Idempotent."""
    return await _dismissal(class_id, rule_id, request, ctx, dismissed=True)


@router.delete(
    "/{class_id}/dismissed-rules/{rule_id}",
    response_model=DismissedRulesOut,
    responses={404: {}, 422: {}},
)
async def show_rule_again(
    class_id: str,
    rule_id: str,
    request: Request,
    ctx: Annotated[UserContext, Depends(auth.require_user)],
) -> DismissedRulesOut | JSONResponse:
    """Undo a dismissal. Idempotent: a rule that is not dismissed stays so."""
    return await _dismissal(class_id, rule_id, request, ctx, dismissed=False)
