"""``/api/connections``: the account page's provider connections (ADR 0004 §4, §5).

- ``GET /api/connections``: the user's connections (provider, status, error category, dates);
  never a secret or ``public``.
- ``POST /api/connections/mowl``: connect or reconnect MOWL with email and password (JSON, CSRF
  checked by the middleware, recent authentication and, with TOTP, step-up). Rate limited per
  user, IP and MOWL email before the sign-in; a success releases the slots.
- ``DELETE /api/connections/mowl``: disconnect (recent authentication); deletes the row. Local
  only: MOWL has no known revoke endpoint.

The password never reaches a log, an event, an error body or a URL: the access log records
method, path and status only; validation errors of this router drop the ``input`` (the app's
handler, ``auth.setup``); failures answer with the category alone.
"""

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, SecretStr

from classbeat import auth
from classbeat.connections.errors import ConnectionFailure
from classbeat.connections.mowl_account import PROVIDER, connect_mowl
from classbeat.connections.repository import ConnectionInfo, disconnect, list_connections
from classbeat.context import UserContext
from classbeat.db import system_session
from classbeat.emailnorm import normalize_email

router = APIRouter(prefix="/api/connections", tags=["connections"])

PASSWORD_MAX = 1024
DEFAULT_RETRY_AFTER = 60

# HTTP status per failure category; the body carries the category only.
_STATUS = {
    "invalid_credentials": 400,
    "rate_limited": 429,
    "upstream_unavailable": 503,
    "key_unavailable": 503,
    "decrypt_failed": 409,
    "needs_reconnect": 409,
    "not_connected": 409,
    "unknown": 502,
}


class MowlConnectIn(BaseModel):
    email: auth.EmailAddress
    password: SecretStr = Field(min_length=1, max_length=PASSWORD_MAX)


class ConnectionOut(BaseModel):
    provider: str
    status: str
    last_error: str | None
    connected_at: datetime
    last_used_at: datetime | None
    expires_at: datetime | None

    @classmethod
    def of(cls, info: ConnectionInfo) -> "ConnectionOut":
        return cls(
            provider=info.provider,
            status=info.status,
            last_error=info.last_error,
            connected_at=info.connected_at,
            last_used_at=info.last_used_at,
            expires_at=info.expires_at,
        )


class ConnectionsOut(BaseModel):
    connections: list[ConnectionOut]


def _failure_response(failure: ConnectionFailure) -> JSONResponse:
    headers: dict[str, str] = {}
    if failure.category == "rate_limited":
        headers["Retry-After"] = str(failure.retry_after or DEFAULT_RETRY_AFTER)
    return JSONResponse(
        {"detail": {"reason": failure.category}},
        status_code=_STATUS.get(failure.category, 502),
        headers=headers,
    )


async def _event(request: Request, ctx: UserContext, kind: str, **detail: str) -> None:
    """An ``auth_events`` row: the provider and, on failure, the category. Never a secret."""
    async with system_session(request.app.state.session_factory) as db:
        await auth.record_event(
            db, kind, user_id=ctx.user_id, ip=auth.request_ip(request), detail=detail
        )


@router.get("")
async def get_connections(
    request: Request, ctx: Annotated[UserContext, Depends(auth.require_user)]
) -> ConnectionsOut:
    rows = await list_connections(request.app.state.session_factory, ctx)
    return ConnectionsOut(connections=[ConnectionOut.of(row) for row in rows])


@router.post(
    "/mowl", response_model=ConnectionOut, responses={400: {}, 409: {}, 429: {}, 502: {}, 503: {}}
)
async def connect_mowl_route(
    body: MowlConnectIn,
    request: Request,
    ctx: Annotated[UserContext, Depends(auth.require_recent_auth)],
) -> ConnectionOut | JSONResponse:
    factory = request.app.state.session_factory
    attempt = await request.app.state.limiter.begin(
        auth.connect_buckets(
            PROVIDER, str(ctx.user_id), auth.request_ip(request), normalize_email(body.email)
        )
    )
    if not attempt.allowed:
        raise auth.RateLimited(attempt.retry_after)
    failure: ConnectionFailure | None = None
    try:
        await connect_mowl(
            factory, request.app.state.keyring, ctx, body.email, body.password.get_secret_value()
        )
    except ConnectionFailure as error:
        failure = error
    if failure is not None:
        await _event(
            request, ctx, "connection_failed", provider=PROVIDER, category=failure.category
        )
        return _failure_response(failure)
    await attempt.succeed()
    await _event(request, ctx, "connection_connected", provider=PROVIDER)
    rows = [row for row in await list_connections(factory, ctx) if row.provider == PROVIDER]
    if not rows:  # a parallel disconnect removed it meanwhile
        return _failure_response(ConnectionFailure("not_connected"))
    return ConnectionOut.of(rows[0])


@router.delete("/mowl", status_code=204)
async def disconnect_mowl(
    request: Request, ctx: Annotated[UserContext, Depends(auth.require_recent_auth)]
) -> Response:
    """Delete the token. Answers 204 whether or not a connection existed."""
    if await disconnect(request.app.state.session_factory, ctx, PROVIDER):
        await _event(request, ctx, "connection_disconnected", provider=PROVIDER)
    return Response(status_code=204)
