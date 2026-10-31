"""What the class API shows of song picking (ADR 0008 sections 5, 8 and 9; issue CL1).

Everything is read in the caller's ``user_session`` and filters on ``owner_id`` itself as well as
through row security, so another user's class, revision or playlist is simply not found.

- ``songs_state``: the class's newest ``song_list`` revision and the job that made it, as
  ``{state, job_id, revision_id, content_hash, expires_at}``. ``state`` is ``none`` (no list),
  ``waiting`` (the job waits for approval and the list is not past ``expires_at``), ``approved``
  (``approved_at`` is set, whatever the job does afterwards), ``expired`` or ``cancelled``. The
  list's own status (``list_status``) adds ``superseded``: a list that is not the newest
  ``song_list`` of its class (or whose job waits on another revision) can no longer be approved.
- ``get_song_list``: one ``song_list`` revision for the page: the songs **without ids**, the
  totals, the ``new`` marks and the note as plain text, plus its status. The stored hash is
  re-checked against the content (``classbeat.songs.song_list.load``) before anything is shown.
- ``playlists_of``: the class's final playlists. A playlist's link is built here from the stored
  id (``https://open.spotify.com/playlist/<id>``), never from Spotify data, and goes only into the
  owner's own class view and class delete answer (ADR 0008 section 2, "Privacy wording").
- ``music_playlists``: every playlist classbeat made for the user, for the Music page (ADR 0013
  sections 4 and 9): the class, the name, the owner's link (as ``playlists_of`` builds it) and up
  to four of its songs (title and the pinned-host cover) from the ``song_list`` it was made from.
- ``playlists_left``: the data of the Disconnect and consent dialogs (names, never a link).

No id of Spotify appears anywhere else, and nothing here logs.
"""

import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Final, Literal
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from classbeat.context import UserContext, require_context
from classbeat.covers import cover_url
from classbeat.db import user_session
from classbeat.playlists.common import POOL_NAME, SPOTIFY_ID
from classbeat.songs import song_list
from classbeat.songs.song_list import SongListContent

Factory = async_sessionmaker[AsyncSession]

DETACHED_NOTE: Final = "In your earlier Spotify account."
PLAYLIST_URL: Final = "https://open.spotify.com/playlist/"
TRACK_URL: Final = "https://open.spotify.com/track/"
SongsStateName = Literal["none", "waiting", "approved", "expired", "superseded", "cancelled"]
ListStatus = Literal["waiting", "approved", "expired", "superseded", "cancelled"]

_JOB = (
    "j.id AS job_id, j.state AS job_state, j.approved_at, j.expires_at, j.approval_revision_id, "
    "coalesce(j.expires_at <= now(), false) AS past_expiry"
)
_NEWEST = text(
    f"SELECT r.id, r.content_hash, {_JOB} FROM class_revisions r "  # noqa: S608 - constants only
    "LEFT JOIN jobs j ON j.owner_id = r.owner_id AND j.id = r.job_id "
    "WHERE r.owner_id = :owner_id AND r.class_id = :class_id AND r.kind = 'song_list' "
    "ORDER BY r.number DESC LIMIT 1"
)
_LIST = text(
    "SELECT r.id, r.number, r.created_at, r.parent_id, p.number AS parent_number, "  # noqa: S608
    "r.content_hash, (r.number = (SELECT max(n.number) FROM class_revisions n "
    "WHERE n.owner_id = r.owner_id AND n.class_id = r.class_id AND n.kind = 'song_list')) "
    f"AS newest, {_JOB} FROM class_revisions r "
    "LEFT JOIN class_revisions p ON p.owner_id = r.owner_id AND p.id = r.parent_id "
    "LEFT JOIN jobs j ON j.owner_id = r.owner_id AND j.id = r.job_id "
    "WHERE r.owner_id = :owner_id AND r.class_id = :class_id AND r.id = :revision_id "
    "AND r.kind = 'song_list'"
)
# The current draft's playlist is the one of the song list the draft was made from
# (``music.song_list_id``, ADR 0008 section 5); compared as text so a malformed value is no match.
_PLAYLISTS = text(
    "SELECT p.name, p.playlist_id, p.state, p.detached_at IS NOT NULL AS detached, "
    "(p.revision_id IS NOT NULL AND p.revision_id::text = (SELECT cur.music ->> 'song_list_id' "
    "FROM classes c JOIN class_revisions cur ON cur.owner_id = c.owner_id "
    "AND cur.class_id = c.id AND cur.id = c.current_revision_id "
    "WHERE c.owner_id = p.owner_id AND c.id = p.class_id)) AS for_current "
    "FROM class_playlists p WHERE p.owner_id = :owner_id AND p.class_id = :class_id "
    "ORDER BY p.created_at, p.id"
)
_LEFT_CLASSES = text(
    "SELECT c.id AS class_id, c.title AS class_title, p.name, "
    "p.detached_at IS NOT NULL AS detached FROM class_playlists p "
    "JOIN classes c ON c.owner_id = p.owner_id AND c.id = p.class_id "
    "WHERE p.owner_id = :owner_id "
    "ORDER BY p.created_at, p.id LIMIT 500"
)
_MUSIC_PLAYLISTS = text(
    "SELECT p.class_id, c.title AS class_title, p.name, p.playlist_id, p.state, p.revision_id, "
    "p.detached_at IS NOT NULL AS detached FROM class_playlists p "
    "JOIN classes c ON c.owner_id = p.owner_id AND c.id = p.class_id "
    "WHERE p.owner_id = :owner_id ORDER BY p.created_at DESC, p.id LIMIT :limit"
)
_POOL_EXISTS = text(
    "SELECT 1 FROM spotify_pool WHERE owner_id = :owner_id AND playlist_id IS NOT NULL"
)

_WHITESPACE = re.compile(r"\s+")


def plain(value: str) -> str:
    """A model-written sentence as plain text: control characters and runs of whitespace are one
    space. The page renders it as text, never as markup."""
    printable = "".join(c if c.isprintable() else " " for c in value)
    return _WHITESPACE.sub(" ", printable).strip()


def link_of(playlist_id: str) -> str | None:
    """The owner's own link to a playlist they hold, built from a stored id; None when the id is
    not 22 letters and digits (it never reaches a URL then)."""
    return PLAYLIST_URL + playlist_id if SPOTIFY_ID.fullmatch(playlist_id) else None


def track_link(spotify_id: str) -> str | None:
    """The link back to one song in Spotify (its design guidelines ask for one wherever Spotify
    metadata is shown), built here from the stored track id of a checked list; None when the id
    is not 22 letters and digits (it never reaches a URL then)."""
    return TRACK_URL + spotify_id if SPOTIFY_ID.fullmatch(spotify_id) else None


def list_status(
    *,
    newest: bool,
    job_state: str | None,
    approved_at: datetime | None,
    past_expiry: bool,
    approval_revision_id: UUID | None,
    revision_id: UUID,
) -> ListStatus:
    """The status of a ``song_list`` (the order is ADR 0005 section 2's): approved once and for
    all; else superseded when a newer list exists or the job waits on another one; else expired
    (the job's state, or waiting past ``expires_at``); else waiting; else cancelled."""
    if approved_at is not None:
        return "approved"
    if not newest:
        return "superseded"
    if job_state == "waiting_for_approval":
        if approval_revision_id != revision_id:
            return "superseded"
        return "expired" if past_expiry else "waiting"
    return "expired" if job_state == "expired" else "cancelled"


@dataclass(frozen=True, slots=True)
class SongsState:
    state: SongsStateName
    job_id: UUID | None = None
    revision_id: UUID | None = None
    content_hash: str | None = None
    expires_at: datetime | None = None


async def songs_state(db: AsyncSession, owner_id: UUID, class_id: UUID) -> SongsState:
    row = (await db.execute(_NEWEST, {"owner_id": owner_id, "class_id": class_id})).first()
    if row is None:
        return SongsState("none")
    status = list_status(
        newest=True,
        job_state=row.job_state,
        approved_at=row.approved_at,
        past_expiry=bool(row.past_expiry),
        approval_revision_id=row.approval_revision_id,
        revision_id=row.id,
    )
    return SongsState(status, row.job_id, row.id, bytes(row.content_hash).hex(), row.expires_at)


@dataclass(frozen=True, slots=True)
class PlaylistRef:
    name: str
    link: str | None
    detached: bool
    note: str | None = None


@dataclass(frozen=True, slots=True)
class Playlists:
    """The class's final playlists, oldest first; ``current`` is the one of the current draft."""

    current: PlaylistRef | None
    all: tuple[PlaylistRef, ...]


async def playlists_of(db: AsyncSession, owner_id: UUID, class_id: UUID) -> Playlists:
    """Every ``class_playlists`` row of the class, detached ones too (they are still in the
    user's Spotify). A link only for a verified playlist: one that is only ``created`` has not
    been compared with the approved songs yet."""
    rows = (await db.execute(_PLAYLISTS, {"owner_id": owner_id, "class_id": class_id})).all()
    refs: list[PlaylistRef] = []
    current: PlaylistRef | None = None
    for r in rows:
        detached = bool(r.detached)
        # A detached playlist belongs to an earlier Spotify account: its name, no link.
        ref = PlaylistRef(
            r.name,
            link_of(r.playlist_id) if r.state == "verified" and not detached else None,
            detached,
            DETACHED_NOTE if detached else None,
        )
        refs.append(ref)
        if r.for_current:
            current = ref  # the newest one wins
    return Playlists(current, tuple(refs))


@dataclass(frozen=True, slots=True)
class SongListView:
    id: UUID
    number: int
    created_at: datetime
    parent_id: UUID | None
    parent_number: int | None
    content_hash: str
    status: ListStatus
    job_id: UUID | None
    expires_at: datetime | None
    approved_at: datetime | None
    content: SongListContent


async def get_song_list(
    factory: Factory, ctx: UserContext, class_id: UUID, revision_id: UUID
) -> SongListView | Literal["unreadable"] | None:
    """The user's own ``song_list`` of that class; None for any other id; ``"unreadable"`` when
    its stored hash or content no longer checks (the caller answers 500, never a changed list)."""
    require_context(ctx)
    async with user_session(factory, ctx) as db:
        params: dict[str, Any] = {
            "owner_id": ctx.user_id,
            "class_id": class_id,
            "revision_id": revision_id,
        }
        row = (await db.execute(_LIST, params)).first()
        if row is None:
            return None
        stored = await song_list.load(db, ctx.user_id, class_id, revision_id)
    if stored is None or stored.content_hash != bytes(row.content_hash):
        return "unreadable"
    status = list_status(
        newest=bool(row.newest),
        job_state=row.job_state,
        approved_at=row.approved_at,
        past_expiry=bool(row.past_expiry),
        approval_revision_id=row.approval_revision_id,
        revision_id=row.id,
    )
    return SongListView(
        row.id,
        row.number,
        row.created_at,
        row.parent_id,
        row.parent_number,
        bytes(row.content_hash).hex(),
        status,
        row.job_id,
        row.expires_at,
        row.approved_at,
        stored.content,
    )


MUSIC_PLAYLISTS_LIMIT: Final = 100
MOSAIC_SONGS: Final = 4


@dataclass(frozen=True, slots=True)
class MosaicSong:
    title: str
    cover: str | None


@dataclass(frozen=True, slots=True)
class MusicPlaylist:
    class_id: UUID
    class_title: str
    name: str
    link: str | None
    detached: bool
    note: str | None
    songs: tuple[MosaicSong, ...]


async def music_playlists(factory: Factory, ctx: UserContext) -> tuple[MusicPlaylist, ...]:
    """The caller's own playlists, newest first (at most 100). Another user's class or playlist is
    never read: every statement filters on ``owner_id`` as well as running under row security.
    A playlist whose song list no longer checks is listed without songs, never with others."""
    require_context(ctx)
    out: list[MusicPlaylist] = []
    async with user_session(factory, ctx) as db:
        rows = (
            await db.execute(
                _MUSIC_PLAYLISTS, {"owner_id": ctx.user_id, "limit": MUSIC_PLAYLISTS_LIMIT}
            )
        ).all()
        for r in rows:
            detached = bool(r.detached)
            songs: tuple[MosaicSong, ...] = ()
            if r.revision_id is not None:
                stored = await song_list.load(db, ctx.user_id, r.class_id, r.revision_id)
                if stored is not None:
                    songs = tuple(
                        MosaicSong(plain(s.title), cover_url(s.cover))
                        for s in stored.content.songs[:MOSAIC_SONGS]
                    )
            out.append(
                MusicPlaylist(
                    r.class_id,
                    plain(r.class_title),
                    r.name,
                    link_of(r.playlist_id) if r.state == "verified" and not detached else None,
                    detached,
                    DETACHED_NOTE if detached else None,
                    songs,
                )
            )
    return tuple(out)


@dataclass(frozen=True, slots=True)
class PlaylistsLeft:
    """What a Disconnect or a consent revoke leaves in the user's Spotify: the working playlist
    (its name only, when there is one) and the class playlists, detached ones too (class and
    name; no link)."""

    pool_playlist: str | None
    class_playlists: tuple[Any, ...]


async def playlists_left(factory: Factory, ctx: UserContext) -> PlaylistsLeft:
    require_context(ctx)
    params = {"owner_id": ctx.user_id}
    async with user_session(factory, ctx) as db:
        pool = (await db.execute(_POOL_EXISTS, params)).first() is not None
        rows = tuple((await db.execute(_LEFT_CLASSES, params)).all())
    return PlaylistsLeft(POOL_NAME if pool else None, rows)
