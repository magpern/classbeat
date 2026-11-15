"""The player connection's routes (ADR 0034 section 3). Web only.

| Route | Does |
|---|---|
| ``GET /api/connections/player`` | status: never a token or a link id |
| ``POST /api/connections/player/start`` | recent auth with step-up, CSRF, 10 per user and hour |
| ``GET /connections/player/callback`` | the redirect target; always a 302 to ``/account/player`` |
| ``DELETE /api/connections/player`` | disconnect (recent auth with step-up, CSRF) |

Linking needs the OpenID Connect provider (``app.state.oidc``: the issuer and the pairwise key)
and a player client (``app.state.player_source``, a ``PlayerSourceHttp``); without either, start
and callback answer 404 ``feature_off``. Status and disconnect always work, so a user can end a
link after the feature is switched off. CSRF and the Origin check are the middleware's for every
unsafe method. The callback reads its query by hand: nothing it says is logged.
"""

from typing import Annotated, Final
from urllib.parse import urlencode, urlsplit
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from pydantic import BaseModel

from classbeat import auth
from classbeat.connections.repository import list_connections
from classbeat.context import UserContext
from classbeat.db import system_session
from classbeat.player_link import connect
from classbeat.player_link.link_policy import CALLBACK_PATH, callback_uri
from classbeat.player_link.source_http import PlayerSourceHttp

PROVIDER: Final = "player"
ACCOUNT_PAGE: Final = "/account/player"

_PROBE = UUID(int=0)  # only to learn whether the provider is on and its issuer
api = APIRouter(prefix="/api/connections/player", tags=["player"])
callback = APIRouter(tags=["player"])


def player_source(request: Request) -> PlayerSourceHttp | None:
    found = getattr(request.app.state, "player_source", None)
    return found if isinstance(found, PlayerSourceHttp) else None


def _binding(request: Request, ctx: UserContext) -> auth.PlayerBinding | None:
    return auth.player_binding(getattr(request.app.state, "oidc", None), ctx.user_id)


def _linking(request: Request, ctx: UserContext | None) -> tuple[PlayerSourceHttp, str]:
    """The player client and classbeat's issuer, or 404 ``feature_off``."""
    http = player_source(request)
    probe = auth.player_binding(getattr(request.app.state, "oidc", None), _PROBE)
    if http is None or probe is None:
        raise HTTPException(status_code=404, detail={"reason": "feature_off"})
    return http, probe.issuer


class StatusOut(BaseModel):
    status: str  # not_connected, connected, needs_reconnect
    last_error: str | None
    connected_at: str | None
    link_enabled: bool


class StartOut(BaseModel):
    link_url: str


class DisconnectOut(BaseModel):
    disconnected: bool
    link_may_remain: bool  # "the link may still be listed on the player's site"


async def _event(request: Request, ctx: UserContext, kind: str, **detail: str) -> None:
    """An ``auth_events`` row: the provider and a category only, never a token, code or state."""
    async with system_session(request.app.state.session_factory) as db:
        await auth.record_event(
            db, kind, user_id=ctx.user_id, ip=auth.request_ip(request), detail=detail
        )


@api.get("")
async def status(
    request: Request, ctx: Annotated[UserContext, Depends(auth.require_user)]
) -> StatusOut:
    rows = await list_connections(request.app.state.session_factory, ctx)
    found = next((row for row in rows if row.provider == PROVIDER), None)
    enabled = player_source(request) is not None and _binding(request, ctx) is not None
    if found is None:
        return StatusOut(
            status="not_connected", last_error=None, connected_at=None, link_enabled=enabled
        )
    return StatusOut(
        status=found.status,
        last_error=found.last_error,
        connected_at=found.connected_at.isoformat(),
        link_enabled=enabled,
    )


@api.post("/start", response_model=StartOut, responses={404: {}, 429: {}})
async def start(
    request: Request,
    ctx: Annotated[UserContext, Depends(auth.require_recent_auth)],
    info: Annotated[auth.SessionInfo | None, Depends(auth.current_session)],
) -> StartOut:
    http, _ = _linking(request, ctx)
    binding = _binding(request, ctx)
    if info is None or binding is None:  # for the type checker: both hold after the checks
        raise HTTPException(status_code=401, detail={"reason": "not_authenticated"})
    attempt = await request.app.state.limiter.begin(
        auth.player_link_start_buckets(str(ctx.user_id))
    )
    if not attempt.allowed:
        raise auth.RateLimited(attempt.retry_after)
    url = await connect.start(request.app.state.session_factory, http, ctx, info.id, binding)
    return StartOut(link_url=url)


def _single(request: Request, name: str) -> str | None:
    values = request.query_params.getlist(name)
    if len(values) != 1:
        return None if not values else ""  # a repeated parameter is no valid value
    return values[0]


def _at_fixed_callback(request: Request, issuer: str) -> bool:
    fixed = urlsplit(callback_uri(issuer))
    return request.url.netloc.lower() == fixed.netloc.lower() and request.url.path == fixed.path


def _redirect(result: connect.Result) -> RedirectResponse:
    query = {"result": result.category}
    if result.old_link_listed:
        query["note"] = "old_link_may_remain"
    response = RedirectResponse(f"{ACCOUNT_PAGE}?{urlencode(query)}", status_code=302)
    response.headers["Cache-Control"] = "no-store"
    response.headers["Referrer-Policy"] = "no-referrer"
    return response


@callback.get(CALLBACK_PATH, include_in_schema=False)
async def player_callback(
    request: Request,
    ctx: Annotated[UserContext | None, Depends(auth.current_context)],
    info: Annotated[auth.SessionInfo | None, Depends(auth.current_session)],
) -> RedirectResponse:
    http, issuer = _linking(request, ctx)
    result = await connect.finish(
        request.app.state.session_factory,
        request.app.state.keyring,
        http,
        ctx,
        None if info is None else info.id,
        None if ctx is None else _binding(request, ctx),
        at_fixed_callback=_at_fixed_callback(request, issuer),
        code=_single(request, "code"),
        state=_single(request, "state"),
        error=_single(request, "error"),
    )
    if ctx is not None:
        if result.connected:
            await _event(request, ctx, "connection_connected", provider=PROVIDER)
        else:
            await _event(
                request, ctx, "connection_failed", provider=PROVIDER, category=result.category
            )
    return _redirect(result)


@api.delete("")
async def disconnect(
    request: Request, ctx: Annotated[UserContext, Depends(auth.require_recent_auth)]
) -> DisconnectOut:
    done = await connect.disconnect(
        request.app.state.session_factory, request.app.state.keyring, player_source(request), ctx
    )
    if done.removed:
        await _event(request, ctx, "connection_disconnected", provider=PROVIDER)
    return DisconnectOut(disconnected=done.removed, link_may_remain=done.link_may_remain)
