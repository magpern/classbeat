"""``GET /api/music/swedebeat-mixes``: the user's Swedebeat mixes as MOWL lists them, for the
new-class builder's "Use my Swedebeat mix" picker (ADR 0016 section 3, A2). Web only; shaped like
``GET /api/music/mowl-playlists`` (``classbeat.design.routes``).

- **Flag first.** With the effective ``swedebeat_enabled`` flag off the answer is 404
  ``feature_off`` before any rate-limit slot, connection or provider call.
- **Needs the user's MOWL connection and no Swedebeat token**: the list goes through MOWL
  (``POST v1/swedebeat/Playlists``, the library's ``swedebeat_mixes``) with the user's MOWL token,
  in the web process and through the existing MOWL-only opener. No new host.
- **Rate limited** per user and per IP (``auth.swedebeat_mixes_buckets``, every request counts)
  before the connection is opened.
- **Answers** per mix its opaque ``mix_id`` (what a design job names), a cleaned ``name``, the
  listed length in seconds and whether the user has an analysis of it (``analysed``, from their
  own ``swedebeat_mix_maps`` rows, ids only). Never a MOWL playlist id, URL or token. Failures
  answer with their category only, as ``mowl-playlists`` does. A GET writes no state.
"""

from typing import Annotated, Final

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from mowl import api as mowl_api
from pydantic import BaseModel

from classbeat import auth
from classbeat.connections import ConnectionFailure, open_connection, run_library_call
from classbeat.connections.swedebeat_account import swedebeat_access
from classbeat.connections.swedebeat_maps import analysed_mix_ids
from classbeat.context import UserContext
from classbeat.egress import OutboundBlocked
from classbeat.operations.swedebeat import parse_mixes

router = APIRouter(prefix="/api/music", tags=["music"])

DEFAULT_RETRY_AFTER: Final = 60
_STATUS: Final = {
    "not_connected": 409,
    "needs_reconnect": 409,
    "decrypt_failed": 409,
    "rate_limited": 429,
    "upstream_unavailable": 503,
    "key_unavailable": 503,
}


class MixOut(BaseModel):
    mix_id: str
    name: str
    duration_seconds: int | None
    analysed: bool


class MixesOut(BaseModel):
    mixes: list[MixOut]


@router.get(
    "/swedebeat-mixes",
    response_model=MixesOut,
    responses={404: {}, 409: {}, 429: {}, 502: {}, 503: {}},
)
async def swedebeat_mixes(
    request: Request, ctx: Annotated[UserContext, Depends(auth.require_user)]
) -> MixesOut | JSONResponse:
    factory = request.app.state.session_factory
    access = await swedebeat_access(request.app.state.settings, factory)
    if not access.enabled:
        raise HTTPException(status_code=404, detail={"reason": "feature_off"})
    attempt = await request.app.state.limiter.begin(
        auth.swedebeat_mixes_buckets(str(ctx.user_id), auth.request_ip(request))
    )
    if not attempt.allowed:
        raise auth.RateLimited(attempt.retry_after)
    failure: ConnectionFailure | None = None
    answer: object = None
    try:
        opened = await open_connection(factory, request.app.state.keyring, ctx, "mowl")
        creds = opened.credentials
        answer = await run_library_call(lambda: mowl_api.swedebeat_mixes(creds), sign_in=False)
    except ConnectionFailure as error:
        failure = error
    except OutboundBlocked:  # the host only is known; nothing of it goes back
        return JSONResponse({"detail": {"reason": "outbound_blocked"}}, status_code=502)
    if failure is not None:
        headers: dict[str, str] = {}
        if failure.category == "rate_limited":
            headers["Retry-After"] = str(failure.retry_after or DEFAULT_RETRY_AFTER)
        return JSONResponse(
            {"detail": {"reason": failure.category}},
            status_code=_STATUS.get(failure.category, 502),
            headers=headers,
        )
    analysed = await analysed_mix_ids(factory, ctx)
    return MixesOut(
        mixes=[
            MixOut(
                mix_id=m.mix_id,
                name=m.name,
                duration_seconds=m.duration_seconds,
                analysed=m.mix_id in analysed,
            )
            for m in parse_mixes(answer)
        ]
    )
