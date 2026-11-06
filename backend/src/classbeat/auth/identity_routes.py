"""Linked sign-in methods on the account page (ADR 0003 sections 1, 5 and 6).

Listing needs a session. Unlinking needs recent authentication (and step-up with a confirmed
TOTP), only touches the signed-in user's own identities, answers 404 for another user's id as for
an unknown one, and refuses to remove the last sign-in method.
"""

from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import select

from classbeat.auth.deps import request_ip, require_recent_auth, require_user
from classbeat.auth.events import record_event
from classbeat.auth.flowtools import factory
from classbeat.auth.oauth_store import list_identities, remove_identity
from classbeat.auth.tables import identities, users
from classbeat.context import UserContext
from classbeat.db import system_session
from classbeat.emails.notices import Notice, happened, queue

router = APIRouter(prefix="/api/auth", tags=["auth"])


class IdentityOut(BaseModel):
    id: UUID
    provider: str
    email_at_link: str | None
    created_at: datetime


class IdentitiesOut(BaseModel):
    identities: list[IdentityOut]
    has_password: bool
    available: list[str]


@router.get("/identities")
async def get_identities(
    request: Request, ctx: Annotated[UserContext, Depends(require_user)]
) -> IdentitiesOut:
    async with system_session(factory(request)) as db:
        rows = await list_identities(db, ctx.user_id)
        has_password = (
            await db.execute(
                select(users.c.password_hash.is_not(None)).where(users.c.id == ctx.user_id)
            )
        ).scalar_one()
    return IdentitiesOut(
        identities=[IdentityOut.model_validate(row, from_attributes=True) for row in rows],
        has_password=has_password,
        available=sorted(request.app.state.oauth.providers),
    )


@router.delete("/identities/{identity_id}", status_code=204)
async def unlink_identity(
    identity_id: str,
    request: Request,
    background: BackgroundTasks,
    ctx: Annotated[UserContext, Depends(require_recent_auth)],
) -> None:
    try:
        target = UUID(identity_id)
    except ValueError:
        raise HTTPException(status_code=404) from None
    notice: Notice | None = None
    async with system_session(factory(request)) as db:
        # The provider and address are read before the row goes; only a removal mails.
        provider = (
            await db.execute(
                select(identities.c.provider).where(
                    identities.c.id == target, identities.c.user_id == ctx.user_id
                )
            )
        ).scalar_one_or_none()
        email = (
            await db.execute(select(users.c.email).where(users.c.id == ctx.user_id))
        ).scalar_one_or_none()
        result = await remove_identity(db, ctx.user_id, target)
        if result == "removed":
            await record_event(db, "identity_unlinked", user_id=ctx.user_id, ip=request_ip(request))
            if provider is not None and email is not None:
                notice = happened("identity_unlinked", email, provider=provider)
    queue(background, request, notice)
    if result == "gone":
        raise HTTPException(status_code=404)  # unknown, or another user's: the same answer
    if result == "last":
        raise HTTPException(status_code=409, detail={"reason": "last_sign_in_method"})
