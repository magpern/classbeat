"""The account page's view of the player consent (ADR 0034 sections 1 and 6).

``GET /api/auth/player-consent`` answers whether the provider is on and, for the signed-in user,
when they consented (``null`` when not, or withdrawn). ``DELETE`` withdraws it: the next sign-in
from the player asks again, and an ``oidc_consent_withdrawn`` event is written. The grant row
itself (its ``first_issued_at``) stays.
"""

from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request

from classbeat.auth.deps import request_ip, require_user
from classbeat.auth.events import record_event
from classbeat.auth.oidc.grant_store import consent_at, withdraw_consent
from classbeat.auth.oidc.provider import Provider
from classbeat.context import UserContext
from classbeat.db import system_session

router = APIRouter(prefix="/api/auth/player-consent", tags=["auth"])


def _provider(request: Request) -> Provider | None:
    return getattr(request.app.state, "oidc", None)


def _iso(value: datetime | None) -> str | None:
    return None if value is None else value.isoformat()


@router.get("")
async def read_consent(
    request: Request, ctx: Annotated[UserContext, Depends(require_user)]
) -> dict[str, Any]:
    provider = _provider(request)
    if provider is None:
        return {"enabled": False, "consent_at": None}
    async with system_session(request.app.state.session_factory) as db:
        given = await consent_at(db, ctx.user_id, provider.client.client_id)
    return {"enabled": True, "consent_at": _iso(given)}


@router.delete("")
async def withdraw(
    request: Request, ctx: Annotated[UserContext, Depends(require_user)]
) -> dict[str, Any]:
    provider = _provider(request)
    if provider is None:
        return {"withdrawn": False}
    async with system_session(request.app.state.session_factory) as db:
        withdrawn = await withdraw_consent(db, ctx.user_id, provider.client.client_id)
        if withdrawn:
            await record_event(
                db, "oidc_consent_withdrawn", user_id=ctx.user_id, ip=request_ip(request)
            )
    return {"withdrawn": withdrawn}
