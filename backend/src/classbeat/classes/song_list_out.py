"""The ``song_list`` and ``swedebeat_match`` revisions as ``GET /api/classes/{id}/revisions/{id}``
answers them (ADR 0008 section 9, ADR 0016 section 6). Split out of ``classes.routes`` and
``classes.schemas`` (CLAUDE.md, split on touch): the response models and the two readers that fill
them field by field. Nothing here names a Spotify or catalogue id, a URL other than the server-built
song link and the pinned cover, a hash other than the approval's ``content_hash``, or a token.

A song list's ``unmet`` (issue #1133) is its Not as asked items: closed paths, reason codes and
small numbers (``agent.taste_check_policy``); the page words them from its catalogue. A composer's
list (``v: 3``, #1188) adds its ``preview``: the class designed on the songs, and each song's span.
"""

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from fastapi import Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from classbeat.agent.interpretation_record import InterpretationItem
from classbeat.classes import match_view, song_views
from classbeat.connections.swedebeat_account import swedebeat_enabled
from classbeat.context import UserContext
from classbeat.covers import cover_url


def _refuse(reason: str, status: int) -> JSONResponse:
    return JSONResponse({"detail": {"reason": reason}}, status_code=status)


def _internal() -> JSONResponse:
    """A stored row that no longer checks: the reason only, nothing of the row."""
    return _refuse("internal", 500)


class SongSourceOut(BaseModel):
    kind: Literal["pinned", "playlist", "search"]
    playlist: int | None = None


class SongOut(BaseModel):
    """A song of a list as the page shows it: no bare Spotify id. ``cover`` is the album cover URL
    on the pinned host (ADR 0013 section 9, checked again just before it is returned) or null.
    ``open_spotify`` is the server-built link back to the song (ADR 0008 section 9, Amendment 6)
    or null."""

    position: int
    title: str
    artist: str
    role: str
    bpm: float
    length_sec: int
    new: bool
    cover: str | None = None
    open_spotify: str | None = None
    # A composer's list (v: 3): pinned, an attached playlist (its position in the request's
    # music_sources) or search (ADR 0038 section 10.2).
    source: SongSourceOut | None = None
    # ADR 0040 section 3: "approximate" for a song whose timing is approximate; left out of the
    # answer otherwise, so every other list reads exactly as before.
    timing: Literal["approximate"] | None = Field(default=None, exclude_if=lambda v: v is None)


class PreviewSongOut(BaseModel):
    start: float
    end: float


class PreviewOut(BaseModel):
    """A composer list's ``preview`` (ADR 0038 section 5): the class designed on these songs, and
    each song's span in it. Not the fingerprint or the run id."""

    content: dict[str, Any]
    songs: list[PreviewSongOut]


class SongListOut(BaseModel):
    id: UUID
    number: int
    kind: Literal["song_list"] = "song_list"
    created_at: datetime
    parent_id: UUID | None
    parent_number: int | None
    status: Literal["waiting", "approved", "expired", "superseded", "cancelled"]
    job_id: UUID | None
    expires_at: datetime | None
    approved_at: datetime | None
    content_hash: str
    mode: str
    target_sec: int
    total_sec: int
    crossfade_sec: float
    songs: list[SongOut]
    omitted_no_bpm: int
    note: str
    unmet: list[InterpretationItem] = []
    preview: PreviewOut | None = None  # v: 3 only (#1188)


class MatchWantedOut(BaseModel):
    """The song of the matched list, as plain text."""

    model_config = ConfigDict(extra="forbid")

    title: str
    artist: str


class MatchHitOut(BaseModel):
    """Swedebeat's catalogue hit, as plain text; ``duration`` in whole seconds or null."""

    model_config = ConfigDict(extra="forbid")

    title: str
    artist: str
    duration: int | None


class MatchSongOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    position: int
    status: Literal["found", "unsure", "missing"]
    wanted: MatchWantedOut
    swedebeat: MatchHitOut | None


class SwedebeatMatchOut(BaseModel):
    """A ``swedebeat_match`` revision as the match page needs it (ADR 0016 section 6, #458): the
    songs in play order, the totals and what the approval sends back (``id``, ``content_hash``).
    Nothing else of the stored row: no catalogue id, URL, token, Spotify id, provenance or matcher
    threshold. Every field is set one by one by the route, never copied from the stored JSON."""

    model_config = ConfigDict(extra="forbid")

    id: UUID
    number: int
    kind: Literal["swedebeat_match"] = "swedebeat_match"
    parent_id: UUID
    content_hash: str
    songs: list[MatchSongOut]
    found: int
    unsure: int
    missing: int
    swedebeat_sec: int


async def song_list(
    request: Request, factory: Any, ctx: UserContext, class_id: UUID, revision_id: UUID
) -> SongListOut | SwedebeatMatchOut | JSONResponse:
    """A ``song_list`` revision: songs without ids, totals, ``new`` marks, a plain-text note."""
    view = await song_views.get_song_list(factory, ctx, class_id, revision_id)
    if view is None:
        return await swedebeat_match(request, factory, ctx, class_id, revision_id)
    if view == "unreadable":  # the stored hash or content no longer checks: never shown
        return _internal()
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
                open_spotify=song_views.track_link(s.spotify_id),
                source=None
                if s.source is None
                else SongSourceOut(kind=s.source.kind, playlist=s.source.playlist),
                timing=s.timing,
            )
            for i, s in enumerate(c.songs, start=1)
        ],
        omitted_no_bpm=c.omitted_no_bpm,
        note=song_views.plain(c.note),
        unmet=list(c.unmet),
        preview=None
        if c.preview is None
        else PreviewOut(
            content=c.preview.content,
            songs=[PreviewSongOut(start=t.start, end=t.end) for t in c.preview.music.songs],
        ),
    )


async def swedebeat_match(
    request: Request, factory: Any, ctx: UserContext, class_id: UUID, revision_id: UUID
) -> SwedebeatMatchOut | JSONResponse:
    """A ``swedebeat_match`` revision (ADR 0016 section 6, #458), field by field.

    Flag off: an own match id is 404 ``feature_off`` and only its key is looked up (its content,
    hash and music are not read); any other id stays 404 ``not_found``. Flag on: another user's
    or an unknown id is 404 ``not_found``; a row that no longer checks is 500 ``internal``.
    """
    if not await swedebeat_enabled(request.app.state.settings, factory):
        if await match_view.is_match(factory, ctx, class_id, revision_id):
            return _refuse("feature_off", 404)
        return _refuse("not_found", 404)
    view = await match_view.get_match(factory, ctx, class_id, revision_id)
    if view is None:
        return _refuse("not_found", 404)
    if view == "unreadable":
        return _internal()
    c = view.content
    return SwedebeatMatchOut(
        id=view.id,
        number=view.number,
        parent_id=view.parent_id,
        content_hash=view.content_hash,
        songs=[
            MatchSongOut(
                position=s.position,
                status=s.status,
                wanted=MatchWantedOut(
                    title=song_views.plain(s.wanted.title),
                    artist=song_views.plain(s.wanted.artist),
                ),
                swedebeat=None
                if s.swedebeat is None
                else MatchHitOut(
                    title=song_views.plain(s.swedebeat.title),
                    artist=song_views.plain(s.swedebeat.artist),
                    duration=s.swedebeat.duration,
                ),
            )
            for s in c.songs
        ],
        found=c.found,
        unsure=c.unsure,
        missing=c.missing,
        swedebeat_sec=c.swedebeat_sec,
    )
