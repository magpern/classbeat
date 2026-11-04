"""``GET /api/music/mowl-playlists``: the user's Spotify playlists as MOWL lists them, for the
new-class form's playlist choice (ADR 0006 section 3, D1). Web only.

Signed in. Opens the user's MOWL connection through ``classbeat.connections.open_connection`` and
makes one read, ``mowl.api.spotify_playlists``, through ``run_library_call``; it answers per
playlist only its Spotify id (what the job input names, ``music.spotify_playlist_id``) and its name:
no track counts, MOWL ids, URLs or texts. An entry whose id is not a Spotify playlist id is left
out; names lose control characters and are cut to 200 characters (they are the user's own playlist
names, shown back to them).

A failure answers with its category only, as the connection routes do: ``not_connected``,
``needs_reconnect`` and ``decrypt_failed`` 409, ``rate_limited`` 429 with ``Retry-After``,
``upstream_unavailable`` and ``key_unavailable`` 503, anything else 502. A request the web's
outbound allowlist refuses (threat model T19) answers 502
``outbound_blocked``.

**A GET writes no state** (Architect decision on #119): a 401 or 403 answers ``needs_reconnect``
without flipping the connection, and no ``last_used_at`` or ``last_error`` is recorded (the
job's own reads do that). The one write left is ``open_connection``'s own: a row whose secret no
longer decrypts flips to ``needs_reconnect`` (ADR 0004 section 5, a key-integrity event). No
CSRF (a GET, no state).

**Rate limited** (SR-05, #354) with the fixed-window limiter of the other provider-calling routes
(``auth.mowl_playlists_buckets``: 30 per user and 60 per IP per 10 minutes, every request counts):
a refused request answers 429 ``rate_limited`` with ``Retry-After`` before the connection is
opened and before any MOWL call. The cross-site GET that could trigger single calls (a
``SameSite=Lax`` cookie travels on a top-level navigation) is bounded by it.

``GET /api/music/playlist-covers`` (ADR 0013 section 9, amendment of 2026-11-03; threat model
T72, T73, T97; #537): the image of each of the user's own Spotify playlists, for the New class
picker's tiles. A separate read so the picker renders at once from ``mowl-playlists`` (unchanged)
and its tiles get covers when this answers. It answers only ``{covers: [{spotify_playlist_id,
cover}]}``, at most 500, each cover a whole-string match of ``classbeat.covers.playlist_cover_url``
(nothing else from Spotify: no name, owner or count; never stored, ``Cache-Control: no-store``,
never model input).

- **Its own rate limit** (``auth.playlist_covers_buckets``: 10 per user and 30 per IP per 10
  minutes, every request counts): 429 ``rate_limited`` before anything else.
- **Every other outcome is 200** (but for the app's 500 when the database fails in the sign-in
  lookup or the rate-limit check, which run before the read). ``{covers: []}``, with no Spotify
  call, unless song picking admits the user, no song-picking job of theirs is queued or running
  (``jobs.service.cover_read_admitted``) and no cover-read cooldown is pending. Every provider
  failure of the read (no or a non-connected Spotify connection, a refused or failed refresh, a
  401, the deadline, an allowlist refusal, more than 500 playlists) is ``{covers: []}`` too,
  logged by category only. A database error anywhere in the read (the cooldown and open-job
  checks, opening the connection, starting a cooldown after a 429) is ``{covers: []}`` too, logged
  as ``database_error`` with nothing of the error's text (#470); a gate that cannot answer never
  gives covers. Both gates (song picking and the open-job check, and the cooldown) are checked
  before the connection is opened and **again** right before the listing is sent, after any
  refresh (``spotify_call``'s ``before_read``), so a pick admitted during the refresh (its lock
  wait and timeout, up to about 30 s) holds the read; only one admitted during the listing itself
  (about 8 s) can overlap it, the accepted window of ADR 0013 section 9 (#470).
- **The read**: one ``spotify_web.my_playlists(max_pages=10)`` through ``spotify_call`` in its
  read-only mode (existing host and token client; no ``record_result``, a 401 never flips; a
  refresh is shielded, never cancelled). ``egress.call_deadline`` bounds only the listing requests
  (8 s), never the refresh, which keeps its own lock wait and timeout.
- **A Spotify 429** starts the user's cooldown for its ``Retry-After`` (60 s to 1 hour, kept in
  the Postgres rate-limit table so every web process honours it); it touches no job.
- **This GET writes**, a departure from the #119 rule above that a GET writes no state: a token
  rotation (the token's own upkeep under the row lock, ADR 0009 section 6), the token endpoint's
  own flip (Spotify's ``invalid_grant`` or app-setup answer about the stored refresh token),
  ``open_connection``'s flip of an undecryptable row, and the cooldown row. All of them are bounded
  by the rate limit; none is the cover. No CSRF: a cross-site GET gets no readable answer.
"""

import logging
import re
from typing import Annotated, Any, Final

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import JSONResponse
from mowl import api as mowl_api
from mowl import spotify_web
from pydantic import BaseModel
from sqlalchemy.exc import SQLAlchemyError

from classbeat import auth, egress
from classbeat.agent.schemas import clean_text
from classbeat.connections import (
    ConnectionFailure,
    Credentials,
    open_connection,
    run_library_call,
)
from classbeat.context import UserContext
from classbeat.covers import playlist_cover_url
from classbeat.egress import OutboundBlocked
from classbeat.jobs import service as jobs_service
from classbeat.spotify.access import spotify_call
from classbeat.spotify.routes import spotify_http

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/music", tags=["music"])

SPOTIFY_ID: Final = re.compile(r"^[0-9A-Za-z]{22}$")
MAX_NAME: Final = 200
MAX_PLAYLISTS: Final = 500
DEFAULT_RETRY_AFTER: Final = 60
_STATUS: Final = {
    "not_connected": 409,
    "needs_reconnect": 409,
    "decrypt_failed": 409,
    "rate_limited": 429,
    "upstream_unavailable": 503,
    "key_unavailable": 503,
}


class PlaylistOut(BaseModel):
    spotify_playlist_id: str
    name: str


class PlaylistsOut(BaseModel):
    playlists: list[PlaylistOut]


def _listed(answer: object) -> list[PlaylistOut]:
    out: list[PlaylistOut] = []
    entries: list[Any] = answer if isinstance(answer, list) else []  # pyright: ignore[reportUnknownVariableType]
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        playlist_id: object = entry.get("ID")  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
        name: object = entry.get("Name")  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
        if not isinstance(playlist_id, str) or not SPOTIFY_ID.fullmatch(playlist_id):
            continue
        out.append(
            PlaylistOut(
                spotify_playlist_id=playlist_id,
                name=clean_text(name if isinstance(name, str) else "")[:MAX_NAME],
            )
        )
        if len(out) >= MAX_PLAYLISTS:
            break
    return out


@router.get(
    "/mowl-playlists",
    response_model=PlaylistsOut,
    responses={409: {}, 429: {}, 502: {}, 503: {}},
)
async def mowl_playlists(
    request: Request, ctx: Annotated[UserContext, Depends(auth.require_user)]
) -> PlaylistsOut | JSONResponse:
    attempt = await request.app.state.limiter.begin(
        auth.mowl_playlists_buckets(str(ctx.user_id), auth.request_ip(request))
    )
    if not attempt.allowed:
        raise auth.RateLimited(attempt.retry_after)
    failure: ConnectionFailure | None = None
    answer: object = None
    try:
        opened = await open_connection(
            request.app.state.session_factory, request.app.state.keyring, ctx, "mowl"
        )
        creds = opened.credentials
        answer = await run_library_call(lambda: mowl_api.spotify_playlists(creds), sign_in=False)
    except ConnectionFailure as error:
        failure = error
    except OutboundBlocked:  # the host only is known; nothing of it goes back
        return JSONResponse({"detail": {"reason": "outbound_blocked"}}, status_code=502)
    if failure is not None:
        headers: dict[str, str] = {}
        if failure.category == "rate_limited":  # MOWL's only 429 category; not the Spotify set
            headers["Retry-After"] = str(failure.retry_after or DEFAULT_RETRY_AFTER)
        return JSONResponse(
            {"detail": {"reason": failure.category}},
            status_code=_STATUS.get(failure.category, 502),
            headers=headers,
        )
    return PlaylistsOut(playlists=_listed(answer))


# --- the picker's playlist covers (ADR 0013 section 9 amendment, #537) ----------------------

COVER_PAGES: Final = 10  # at most 10 listing requests, 500 playlists (the library fails above)
COVER_SECONDS: Final = 8.0  # the listing requests only, never the refresh
# A Spotify 429 (``quota_exhausted`` when its Retry-After is over an hour, ADR 0009 section 8).
SPOTIFY_429: Final = frozenset({"rate_limited", "quota_exhausted"})


class CoverOut(BaseModel):
    spotify_playlist_id: str
    cover: str


class CoversOut(BaseModel):
    covers: list[CoverOut]


def _list_playlists(creds: Credentials) -> Any:
    with egress.call_deadline(COVER_SECONDS):
        return spotify_web.my_playlists(creds, max_pages=COVER_PAGES)


def _covers(answer: object) -> list[CoverOut]:
    out: list[CoverOut] = []
    entries: list[Any] = answer if isinstance(answer, list) else []  # pyright: ignore[reportUnknownVariableType]
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        playlist_id: object = entry.get("id")  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
        cover = playlist_cover_url(entry.get("image"))  # pyright: ignore[reportUnknownMemberType]
        if not isinstance(playlist_id, str) or not SPOTIFY_ID.fullmatch(playlist_id):
            continue
        if cover is not None:
            out.append(CoverOut(spotify_playlist_id=playlist_id, cover=cover))
        if len(out) >= MAX_PLAYLISTS:
            break
    return out


class _NotAdmitted(Exception):
    """The gates said no at the re-check right before the listing: nothing is sent."""


async def _admitted(state: Any, ctx: UserContext) -> bool:
    """No cooldown runs, song picking admits the user and no song pick of theirs is open."""
    if await state.limiter.cooling_down(str(ctx.user_id)):
        return False
    return await jobs_service.cover_read_admitted(state.session_factory, ctx, song_picking_on=True)


async def _read_covers(request: Request, ctx: UserContext) -> list[CoverOut]:
    if not request.app.state.settings.song_picking:
        return []
    try:
        return await _gated_read(request, ctx)
    except SQLAlchemyError:
        # A gate that cannot answer gives no covers; the error's text (SQL, parameters) is not
        # logged, only its category (#470).
        logger.warning("playlist covers unavailable: database_error")
        return []


async def _gated_read(request: Request, ctx: UserContext) -> list[CoverOut]:
    state = request.app.state
    if not await _admitted(state, ctx):
        return []

    async def still_admitted() -> None:
        # Again after any refresh, right before the listing (#470, ADR 0013 section 9).
        if not await _admitted(state, ctx):
            raise _NotAdmitted

    try:
        answer = await spotify_call(
            state.session_factory,
            state.keyring,
            ctx,
            _list_playlists,
            http=spotify_http(request),
            margin_seconds=state.settings.spotify_refresh_margin_seconds,
            read_only=True,
            before_read=still_admitted,
        )
    except _NotAdmitted:
        logger.info("playlist covers unavailable: not_admitted")
        return []
    except ConnectionFailure as failure:
        if failure.category in SPOTIFY_429:
            await state.limiter.start_cooldown(str(ctx.user_id), failure.retry_after)
        logger.info("playlist covers unavailable: %s", failure.category)
        return []
    except OutboundBlocked:
        logger.info("playlist covers unavailable: outbound_blocked")
        return []
    return _covers(answer)


@router.get("/playlist-covers", response_model=CoversOut, responses={429: {}})
async def playlist_covers(
    request: Request, response: Response, ctx: Annotated[UserContext, Depends(auth.require_user)]
) -> CoversOut:
    attempt = await request.app.state.limiter.begin(
        auth.playlist_covers_buckets(str(ctx.user_id), auth.request_ip(request))
    )
    if not attempt.allowed:
        raise auth.RateLimited(attempt.retry_after)
    response.headers["Cache-Control"] = "no-store"
    return CoversOut(covers=await _read_covers(request, ctx))
