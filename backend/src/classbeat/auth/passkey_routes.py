"""List, rename and remove passkeys on the account page (ADR 0019 sections 1, 7 and 10, #772).

- ``GET /api/auth/passkeys``: signed in, like the identities and sessions lists. Nothing in it is
  secret: label, dates, transports and whether it is synced, never a credential id or key.
- ``PATCH`` and ``DELETE /api/auth/passkeys/{id}``: recent authentication (and the step-up once
  TOTP is confirmed), CSRF and Origin (the middleware), 30 a user and hour, 404 for an unknown id
  and for another user's alike. The delete locks the user row first (section 6) and mails the user.
- **The flag** (section 1): list and remove answer whatever ``CLASSBEAT_PASSKEYS`` says, so
  switching it off never hides a passkey the user has. Rename answers 404 ``feature_off`` when it
  is off, checked before anything else, so the flag state is all it reveals.
- **No last-method rule here** (section 7): passkeys never count toward ADR 0003's rule, so a user
  always keeps a password or an identity and removing a passkey is always allowed.
"""

from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from pydantic import AfterValidator, BaseModel

from classbeat.auth.deps import cached_session, request_ip, require_recent_auth, require_user
from classbeat.auth.events import record_event
from classbeat.auth.flowtools import factory, reserve
from classbeat.auth.passkey_limits import manage_buckets
from classbeat.auth.passkey_policy import MAX_PASSKEYS, clean_label
from classbeat.auth.passkey_store import PasskeyRow, delete_passkey, list_passkeys, rename_passkey
from classbeat.auth.userlock import lock_user
from classbeat.context import UserContext
from classbeat.db import system_session
from classbeat.emails.notices import Notice, happened, queue

router = APIRouter(prefix="/api/auth/passkeys", tags=["passkeys"])


async def require_passkeys(request: Request) -> None:
    """404 ``feature_off`` while ``CLASSBEAT_PASSKEYS`` is off. Listed first in a route's
    parameters, so it answers before the session and recent-authentication checks."""
    if not request.app.state.settings.passkeys:
        raise HTTPException(status_code=404, detail={"reason": "feature_off"})


FeatureOn = Annotated[None, Depends(require_passkeys)]
RecentAuth = Annotated[UserContext, Depends(require_recent_auth)]


def check_label(value: str) -> str:
    label = clean_label(value)
    if label is None:
        raise ValueError("a label is 1 to 64 printable characters")
    return label


Label = Annotated[str, AfterValidator(check_label)]


class PasskeyOut(BaseModel):
    id: UUID
    label: str
    transports: list[str]
    synced: bool  # backup eligible: "synced", else "this device only"
    created_at: datetime
    last_used_at: datetime | None

    @classmethod
    def of(cls, row: PasskeyRow) -> "PasskeyOut":
        return cls(
            id=row.id,
            label=row.label,
            transports=row.transports,
            synced=row.backup_eligible,
            created_at=row.created_at,
            last_used_at=row.last_used_at,
        )


class PasskeysOut(BaseModel):
    passkeys: list[PasskeyOut]
    enabled: bool  # CLASSBEAT_PASSKEYS: off, the page says they can still be removed
    max: int


class RenameIn(BaseModel):
    label: Label


def _own_id(value: str) -> UUID:
    try:
        return UUID(value)
    except ValueError:
        raise HTTPException(status_code=404) from None


@router.get("")
async def get_passkeys(
    request: Request, ctx: Annotated[UserContext, Depends(require_user)]
) -> PasskeysOut:
    async with system_session(factory(request)) as db:
        rows = await list_passkeys(db, ctx.user_id)
    return PasskeysOut(
        passkeys=[PasskeyOut.of(row) for row in rows],
        enabled=request.app.state.settings.passkeys,
        max=MAX_PASSKEYS,
    )


@router.patch("/{passkey_id}")
async def patch_passkey(
    _: FeatureOn, passkey_id: str, body: RenameIn, request: Request, ctx: RecentAuth
) -> PasskeyOut:
    await reserve(request, manage_buckets(str(ctx.user_id)))
    target = _own_id(passkey_id)
    async with system_session(factory(request)) as db:
        row = await rename_passkey(db, ctx.user_id, target, body.label)
        if row is not None:
            await record_event(
                db,
                "passkey_renamed",
                user_id=ctx.user_id,
                ip=request_ip(request),
                detail={"passkey": str(row.id)},
            )
    if row is None:
        raise HTTPException(status_code=404)  # unknown, or another user's: the same answer
    return PasskeyOut.of(row)


@router.delete("/{passkey_id}", status_code=204)
async def remove_passkey(
    passkey_id: str, request: Request, background: BackgroundTasks, ctx: RecentAuth
) -> None:
    await reserve(request, manage_buckets(str(ctx.user_id)))
    target = _own_id(passkey_id)
    notice: Notice | None = None
    async with system_session(factory(request)) as db:
        locked = await lock_user(db, ctx.user_id)  # first (section 6)
        label = None if locked is None else await delete_passkey(db, ctx.user_id, target)
        if label is not None:
            await record_event(
                db,
                "passkey_removed",
                user_id=ctx.user_id,
                ip=request_ip(request),
                detail={"passkey": str(target)},
            )
            info = cached_session(request)  # resolved by require_recent_auth: the address now
            if info is not None:
                notice = happened("passkey_removed", info.email, ctx.user_id, label=label)
    queue(background, request, notice)
    if label is None:
        raise HTTPException(status_code=404)  # unknown, or another user's: the same answer
