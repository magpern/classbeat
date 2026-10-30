"""The Spotify connection's routes (ADR 0009 sections 2, 4, 7 and 9). Web only.

| Route | Does |
|---|---|
| ``GET /api/connections/spotify`` | status: never a token, a Spotify user id or a display name |
| ``POST /api/connections/spotify/start`` | recent auth, CSRF, rate limits; a new flow |
| ``GET /oauth/spotify/callback`` | the redirect target; always a 302 to the account page |
| ``DELETE /api/connections/spotify`` | Disconnect (recent auth, CSRF) |
| ``PUT`` / ``DELETE /api/connections/spotify/pool-consent`` | the standing consent |

With ``CLASSBEAT_SONG_PICKING`` off every route answers 404 ``feature_off`` (a route
dependency, resolved before any other). CSRF and the Origin check are the middleware's for every
unsafe method. The start body has no Client Secret field and refuses unknown fields (a posted
``client_secret`` is 422, and the validation error never echoes input). The callback reads its
query by hand: nothing it says is logged (the access log records the path only), and a
validation error could not echo it either.
"""

from typing import Annotated, Final
from urllib.parse import urlencode, urlsplit
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import BaseModel, ConfigDict, Field

from classbeat import auth, connections
from classbeat.classes import song_views
from classbeat.config import Settings
from classbeat.context import UserContext
from classbeat.db import system_session
from classbeat.spotify import connect
from classbeat.spotify.errors import sentence
from classbeat.spotify.flow import CLIENT_ID_INPUT_MAX, normalize_client_id
from classbeat.spotify.http import SpotifyHttp

PROVIDER: Final = "spotify"
CALLBACK_PATH: Final = "/oauth/spotify/callback"
ACCOUNT_PAGE: Final = "/account/spotify"
# Section 9: the wording the user consents to, and its version (``spotify_pool.consent_version``).
CONSENT_VERSION: Final = 1
CONSENT_TEXT: Final = (
    "classbeat manages one private working playlist in your Spotify account, named classbeat "
    "working copy, to ask for song candidates and have MOWL analyse them. It is overwritten on "
    "each pick; anyone with its link could open it."
)


def _settings(request: Request) -> Settings:
    return request.app.state.settings


async def require_song_picking(request: Request) -> None:
    if not _settings(request).song_picking:
        raise HTTPException(status_code=404, detail={"reason": "feature_off"})


api = APIRouter(
    prefix="/api/connections/spotify",
    tags=["spotify"],
    dependencies=[Depends(require_song_picking)],
)
callback = APIRouter(tags=["spotify"], dependencies=[Depends(require_song_picking)])


def spotify_http(request: Request) -> SpotifyHttp:
    """The app's Spotify client (tests replace it with the fake's)."""
    found = getattr(request.app.state, "spotify_http", None)
    if isinstance(found, SpotifyHttp):
        return found
    return SpotifyHttp(timeout_seconds=_settings(request).spotify_refresh_timeout_seconds)


class StatusOut(BaseModel):
    status: str  # not_connected, connected, needs_reconnect
    client_id: str | None  # the user's own public value
    last_error: str | None
    last_error_message: str | None
    callback_url: str
    pool_consent: bool
    consent_version: int | None
    consent_text: str
    song_picking_enabled: bool
    # The dialogs' data (ADR 0008 section 8): what a consent revoke or Disconnect leaves in the
    # user's own Spotify. Names only, never a link or an id.
    pool_playlist: str | None
    class_playlists: list["ClassPlaylistOut"]


class ClassPlaylistOut(BaseModel):
    class_id: UUID
    class_title: str
    name: str
    detached: bool


class StartIn(BaseModel):
    # No Client Secret field, and nothing else: a posted ``client_secret`` is 422.
    model_config = ConfigDict(extra="forbid")

    client_id: str = Field(min_length=1, max_length=CLIENT_ID_INPUT_MAX)
    confirm_account_change: bool = False


class StartOut(BaseModel):
    authorize_url: str


async def _event(request: Request, ctx: UserContext, kind: str, **detail: str) -> None:
    """An ``auth_events`` row: ids and categories only, never a token, code or state."""
    async with system_session(request.app.state.session_factory) as db:
        await auth.record_event(
            db, kind, user_id=ctx.user_id, ip=auth.request_ip(request), detail=detail
        )


@api.get("")
async def status(
    request: Request, ctx: Annotated[UserContext, Depends(auth.require_user)]
) -> StatusOut:
    found = await connections.spotify_status(request.app.state.session_factory, ctx)
    left = await song_views.playlists_left(request.app.state.session_factory, ctx)
    settings = _settings(request)
    return StatusOut(
        status=found.status,
        client_id=found.client_id,
        last_error=found.last_error,
        last_error_message=None if found.last_error is None else sentence(found.last_error),
        callback_url=settings.spotify_redirect_uri,
        pool_consent=found.pool_consent,
        consent_version=found.consent_version,
        consent_text=CONSENT_TEXT,
        song_picking_enabled=settings.song_picking,
        pool_playlist=left.pool_playlist,
        class_playlists=[
            ClassPlaylistOut(
                class_id=r.class_id,
                class_title=r.class_title,
                name=r.name,
                detached=bool(r.detached),
            )
            for r in left.class_playlists
        ],
    )


@api.post("/start", response_model=StartOut, responses={422: {}, 429: {}})
async def start(
    body: StartIn,
    request: Request,
    ctx: Annotated[UserContext, Depends(auth.require_recent_auth)],
    info: Annotated[auth.SessionInfo | None, Depends(auth.current_session)],
) -> StartOut | JSONResponse:
    if info is None:  # require_recent_auth needs a session; this is only for the type checker
        raise HTTPException(status_code=401, detail={"reason": "not_authenticated"})
    attempt = await request.app.state.limiter.begin(
        auth.spotify_start_buckets(str(ctx.user_id), auth.request_ip(request))
    )
    if not attempt.allowed:
        raise auth.RateLimited(attempt.retry_after)
    client_id = normalize_client_id(body.client_id)
    if client_id is None:
        return JSONResponse({"detail": {"reason": "invalid_client_id"}}, status_code=422)
    url = await connect.start(
        request.app.state.session_factory,
        _settings(request),
        ctx,
        info.id,
        client_id,
        confirm_account_change=body.confirm_account_change,
    )
    return StartOut(authorize_url=url)


def _single(request: Request, name: str) -> str | None:
    values = request.query_params.getlist(name)
    if len(values) != 1:
        return None if not values else ""  # a repeated parameter is no valid value
    return values[0]


def _at_fixed_callback(request: Request, settings: Settings) -> bool:
    fixed = urlsplit(settings.spotify_redirect_uri)
    return request.url.netloc.lower() == fixed.netloc.lower() and request.url.path == fixed.path


def _redirect(result: connect.Result) -> RedirectResponse:
    query = {"result": result.category}
    if result.retry_after is not None:
        query["retry_after"] = str(result.retry_after)
    response = RedirectResponse(f"{ACCOUNT_PAGE}?{urlencode(query)}", status_code=302)
    response.headers["Cache-Control"] = "no-store"
    response.headers["Referrer-Policy"] = "no-referrer"
    return response


@callback.get(CALLBACK_PATH, include_in_schema=False)
async def spotify_callback(
    request: Request,
    ctx: Annotated[UserContext | None, Depends(auth.current_context)],
    info: Annotated[auth.SessionInfo | None, Depends(auth.current_session)],
) -> RedirectResponse:
    settings = _settings(request)
    result = await connect.finish(
        request.app.state.session_factory,
        request.app.state.keyring,
        settings,
        spotify_http(request),
        ctx,
        None if info is None else info.id,
        at_fixed_callback=_at_fixed_callback(request, settings),
        code=_single(request, "code"),
        state=_single(request, "state"),
        error=_single(request, "error"),
    )
    if ctx is not None:
        if result.connected:
            await request.app.state.limiter.release(
                auth.spotify_start_buckets(str(ctx.user_id), auth.request_ip(request))
            )
            await _event(request, ctx, "connection_connected", provider=PROVIDER)
        else:
            await _event(
                request, ctx, "connection_failed", provider=PROVIDER, category=result.category
            )
    return _redirect(result)


@api.delete("", status_code=204)
async def disconnect(
    request: Request, ctx: Annotated[UserContext, Depends(auth.require_recent_auth)]
) -> Response:
    """Disconnect (section 7): ``forget_account`` and the row deleted, in one transaction.
    Locally only (Spotify has no app-side revocation). 204 whether or not one existed."""
    if await connections.disconnect(request.app.state.session_factory, ctx, PROVIDER):
        await _event(request, ctx, "connection_disconnected", provider=PROVIDER)
    return Response(status_code=204)


@api.put("/pool-consent", status_code=204, responses={409: {}})
async def give_pool_consent(
    request: Request, ctx: Annotated[UserContext, Depends(auth.require_recent_auth)]
) -> Response:
    given = await connections.give_pool_consent(
        request.app.state.session_factory, ctx, CONSENT_VERSION
    )
    if not given:
        return JSONResponse({"detail": {"reason": "not_connected"}}, status_code=409)
    await _event(
        request, ctx, "spotify_pool_consent", provider=PROVIDER, version=str(CONSENT_VERSION)
    )
    return Response(status_code=204)


@api.delete("/pool-consent", status_code=204)
async def revoke_pool_consent(
    request: Request, ctx: Annotated[UserContext, Depends(auth.require_recent_auth)]
) -> Response:
    if await connections.revoke_pool_consent(request.app.state.session_factory, ctx):
        await _event(request, ctx, "spotify_pool_revoked", provider=PROVIDER)
    return Response(status_code=204)
