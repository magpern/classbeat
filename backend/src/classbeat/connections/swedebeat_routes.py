"""``/api/connections/swedebeat``: connect and disconnect the user's Swedebeat account (ADR 0015).
Its status is the ``swedebeat`` row of ``GET /api/connections`` and its ``swedebeat`` access block.

- ``POST``: sign in with the Swedebeat email and password (JSON, CSRF by the middleware, recent
  authentication and, with TOTP, step-up), optionally ``force`` after the user confirmed that
  another device may be signed out. 404 ``feature_off`` while the effective flag is off (the
  environment's switch and the admin flag, ``classbeat.flags``). 503 ``app_key_unavailable``
  before the rate limit is spent when this server has no Swedebeat app key. Rate limited per user,
  IP and Swedebeat email before the sign-in, like MOWL; a success releases the slots.
- ``DELETE``: disconnect (recent authentication). Deletes the row; local only. Never gated by the
  flag: a user can always remove what is stored about them.

Failures answer with the category alone: ``invalid_credentials`` (400), ``device_in_use`` (409),
``upstream_unavailable`` (503), ``app_key_unavailable`` (503), ``rate_limited`` (429, with
``Retry-After``), ``unknown`` (502). The password never reaches a log, an event, an error body or
a URL (see ``routes``).
"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, SecretStr

from classbeat import auth
from classbeat.config import Settings
from classbeat.connections.errors import ConnectionFailure
from classbeat.connections.repository import disconnect, list_connections
from classbeat.connections.routes import ConnectionOut, event, failure_response
from classbeat.connections.swedebeat_account import (
    PROVIDER,
    SwedebeatRefusal,
    connect_swedebeat,
    swedebeat_access,
)
from classbeat.context import UserContext
from classbeat.emailnorm import normalize_email

router = APIRouter(prefix="/api/connections/swedebeat", tags=["connections"])

PASSWORD_MAX = 1024
_REFUSAL_STATUS = {"device_in_use": 409, "app_key_unavailable": 503}


class SwedebeatConnectIn(BaseModel):
    email: auth.EmailAddress
    password: SecretStr = Field(min_length=1, max_length=PASSWORD_MAX)
    # True only after the user confirmed that another device (the phone app) may be signed out.
    force: bool = False


def _refusal_response(refusal: SwedebeatRefusal) -> JSONResponse:
    return JSONResponse(
        {"detail": {"reason": refusal.reason}}, status_code=_REFUSAL_STATUS.get(refusal.reason, 502)
    )


@router.post(
    "",
    response_model=ConnectionOut,
    responses={400: {}, 404: {}, 409: {}, 429: {}, 502: {}, 503: {}},
)
async def connect_swedebeat_route(
    body: SwedebeatConnectIn,
    request: Request,
    ctx: Annotated[UserContext, Depends(auth.require_recent_auth)],
) -> ConnectionOut | JSONResponse:
    settings: Settings = request.app.state.settings
    access = await swedebeat_access(settings, request.app.state.session_factory)
    if not access.enabled:
        raise HTTPException(status_code=404, detail={"reason": "feature_off"})
    if not access.available:  # nothing would be sent: spend no rate-limit slot
        return _refusal_response(SwedebeatRefusal("app_key_unavailable"))
    assert settings.app_secret_key is not None  # noqa: S101 - the web app refuses to start without
    factory = request.app.state.session_factory
    attempt = await request.app.state.limiter.begin(
        auth.connect_buckets(
            PROVIDER, str(ctx.user_id), auth.request_ip(request), normalize_email(body.email)
        )
    )
    if not attempt.allowed:
        raise auth.RateLimited(attempt.retry_after)
    failure: ConnectionFailure | None = None
    refusal: SwedebeatRefusal | None = None
    try:
        await connect_swedebeat(
            factory,
            request.app.state.keyring,
            ctx,
            body.email,
            body.password.get_secret_value(),
            force=body.force,
            device_secret=settings.app_secret_key.get_secret_value().encode(),
            app=settings.swedebeat_keys(),
        )
    except ConnectionFailure as error:
        failure = error
    except SwedebeatRefusal as error:
        refusal = error
    if failure is not None:
        await event(request, ctx, "connection_failed", provider=PROVIDER, category=failure.category)
        return failure_response(failure)
    if refusal is not None:
        await event(request, ctx, "connection_failed", provider=PROVIDER, category=refusal.reason)
        return _refusal_response(refusal)
    await attempt.succeed()
    await event(request, ctx, "connection_connected", provider=PROVIDER)
    rows = [row for row in await list_connections(factory, ctx) if row.provider == PROVIDER]
    if not rows:  # a parallel disconnect removed it meanwhile
        return failure_response(ConnectionFailure("not_connected"))
    return ConnectionOut.of(rows[0], account=body.email)


@router.delete("", status_code=204)
async def disconnect_swedebeat(
    request: Request, ctx: Annotated[UserContext, Depends(auth.require_recent_auth)]
) -> Response:
    """Delete the token and device id. Answers 204 whether or not a connection existed."""
    if await disconnect(request.app.state.session_factory, ctx, PROVIDER):
        await event(request, ctx, "connection_disconnected", provider=PROVIDER)
    return Response(status_code=204)
