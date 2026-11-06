"""``/api/connections/mowl/qr``: connect MOWL by scanning a QR code (ADR 0024, #647).

- ``POST``: a new ticket (recent authentication and, with TOTP, step-up, as the password
  connect; CSRF checked by the middleware). Rate limited per user and IP before MOWL is called
  (``auth.mowl_qr_buckets``). Answers ``{ticket_id, image, expires_at}``: our own random id, the
  path of the image on our origin and the end of the wait. Cancels the user's earlier wait.
Every route but ``POST`` finds a wait only for the user **and the session** that started it: the
start passed the recent-authentication check, so the store rides on that session alone (signing
it out ends the chance to store), and another user's or another session's id is ``not_found``.

- ``GET /{ticket_id}``: the wait's status, polled by the page. When the phone has scanned, this
  request (the starting session, still live) stores the token exactly as the password connect does
  (encrypted, ``repository.save``) and records ``connection_connected`` with ``method: qr``.
- ``GET /{ticket_id}/image``: the QR image, ``Cache-Control: no-store``, while the wait runs.
- ``DELETE /{ticket_id}``: cancel (the page was left). 204 either way.

404 ``feature_off`` on every route while ``CLASSBEAT_MOWL_QR_ENABLED`` is off; 404 ``not_found``
for an unknown id or another user's. No answer, log or event carries the ticket's topic key, the
broker password or the token.
"""

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from classbeat import auth
from classbeat.connections import mowl_qr
from classbeat.connections.errors import ConnectionFailure
from classbeat.connections.mowl_account import PROVIDER
from classbeat.connections.repository import list_connections, save
from classbeat.connections.routes import ConnectionOut, event, failure_response
from classbeat.context import UserContext

router = APIRouter(prefix="/api/connections/mowl/qr", tags=["connections"])
METHOD = "qr"


class QrStartOut(BaseModel):
    ticket_id: str
    image: str
    expires_at: datetime


class QrStatusOut(BaseModel):
    status: Literal["waiting", "connected", "expired", "failed", "cancelled"]
    reason: str | None = None
    expires_at: datetime
    connection: ConnectionOut | None = None


def _require_enabled(request: Request) -> mowl_qr.QrWaits:
    if not request.app.state.settings.mowl_qr_enabled:
        raise HTTPException(status_code=404, detail={"reason": "feature_off"})
    waits: mowl_qr.QrWaits = request.app.state.mowl_qr
    return waits


def _session_id(info: auth.SessionInfo | None) -> UUID:
    if info is None:  # require_user passed, so a session exists; never reached
        raise HTTPException(status_code=401, detail={"reason": "unauthenticated"})
    return info.id


def _found(
    waits: mowl_qr.QrWaits, ctx: UserContext, info: auth.SessionInfo | None, ticket_id: str
) -> mowl_qr.QrWait:
    wait = waits.get(ctx.user_id, _session_id(info), ticket_id)
    if wait is None:
        raise HTTPException(status_code=404, detail={"reason": "not_found"})
    return wait


@router.post("", response_model=QrStartOut, responses={404: {}, 429: {}, 502: {}, 503: {}})
async def start_qr(
    request: Request,
    ctx: Annotated[UserContext, Depends(auth.require_recent_auth)],
    info: Annotated[auth.SessionInfo | None, Depends(auth.current_session)],
) -> QrStartOut | JSONResponse:
    waits = _require_enabled(request)
    session_id = _session_id(info)
    attempt = await request.app.state.limiter.begin(
        auth.mowl_qr_buckets(str(ctx.user_id), auth.request_ip(request))
    )
    if not attempt.allowed:
        raise auth.RateLimited(attempt.retry_after)
    failure: ConnectionFailure | None = None
    wait: mowl_qr.QrWait | None = None
    try:
        ticket = await mowl_qr.request_ticket()
        image = await mowl_qr.fetch_image(ticket)
        wait = waits.start(ctx.user_id, session_id, ticket, image)
    except ConnectionFailure as error:
        failure = error
    if failure is not None or wait is None:
        failure = failure or ConnectionFailure("unknown")
        await event(
            request, ctx, "connection_failed",
            provider=PROVIDER, method=METHOD, category=failure.category,
        )  # fmt: skip
        return failure_response(failure)
    return QrStartOut(
        ticket_id=wait.id,
        image=f"{router.prefix}/{wait.id}/image",
        expires_at=wait.expires_at,
    )


async def _store(request: Request, ctx: UserContext, wait: mowl_qr.QrWait, token: str) -> None:
    """The scanned token, stored as the password connect stores it; the wait's outcome."""
    failure: ConnectionFailure | None = None
    try:
        await save(
            request.app.state.session_factory,
            request.app.state.keyring,
            ctx,
            PROVIDER,
            {"token": token},
        )
    except ConnectionFailure as error:
        failure = error
    if failure is not None:
        wait.finish("failed", failure.category)
        await event(
            request, ctx, "connection_failed",
            provider=PROVIDER, method=METHOD, category=failure.category,
        )  # fmt: skip
        return
    wait.finish("connected")
    await event(request, ctx, "connection_connected", provider=PROVIDER, method=METHOD)


@router.get("/{ticket_id}", response_model=QrStatusOut, responses={404: {}})
async def qr_status(
    ticket_id: str,
    request: Request,
    ctx: Annotated[UserContext, Depends(auth.require_user)],
    info: Annotated[auth.SessionInfo | None, Depends(auth.current_session)],
) -> QrStatusOut:
    wait = _found(_require_enabled(request), ctx, info, ticket_id)
    token = wait.take_token()
    if token is not None:
        await _store(request, ctx, wait, token)
    connection: ConnectionOut | None = None
    if wait.state == "connected":
        factory = request.app.state.session_factory
        rows = [row for row in await list_connections(factory, ctx) if row.provider == PROVIDER]
        connection = ConnectionOut.of(rows[0]) if rows else None
    status = wait.state
    shown: Literal["waiting", "connected", "expired", "failed", "cancelled"] = (
        "waiting" if status in ("waiting", "storing", "scanned") else status
    )
    return QrStatusOut(
        status=shown, reason=wait.reason, expires_at=wait.expires_at, connection=connection
    )


@router.get("/{ticket_id}/image", responses={404: {}})
async def qr_image(
    ticket_id: str,
    request: Request,
    ctx: Annotated[UserContext, Depends(auth.require_user)],
    info: Annotated[auth.SessionInfo | None, Depends(auth.current_session)],
) -> Response:
    wait = _found(_require_enabled(request), ctx, info, ticket_id)
    if wait.state != "waiting":
        raise HTTPException(status_code=404, detail={"reason": "not_found"})
    return Response(
        wait.image.content,
        media_type=wait.image.content_type,
        headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"},
    )


@router.delete("/{ticket_id}", status_code=204)
async def cancel_qr(
    ticket_id: str,
    request: Request,
    ctx: Annotated[UserContext, Depends(auth.require_user)],
    info: Annotated[auth.SessionInfo | None, Depends(auth.current_session)],
) -> Response:
    _require_enabled(request).cancel(ctx.user_id, _session_id(info), ticket_id)
    return Response(status_code=204)
