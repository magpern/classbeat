"""Mail preferences and the unsubscribe link (ADR 0022 section 5, ADR 0003 sections 4, 8, 9, 10).

Signed in: ``GET`` and ``PUT /api/mail/preferences`` read and set the user's own choice, both ways,
under the usual Origin and CSRF checks.

Signed out, by the link in an optional mail (``unsubscribe_token``):

- The page ``/unsubscribe#token=...`` is the SPA (a ``GET`` of it, or of any URL, changes nothing:
  mail scanners and link previews fetch links). It posts the token it holds to
  ``POST /api/mail/unsubscribe/confirm`` when the person presses the button.
- ``POST /api/mail/unsubscribe?token=...`` is RFC 8058 one-click (``List-Unsubscribe-Post``), sent
  by the mail client with a form body we ignore.

Both are exempt from the Origin and CSRF check (``middleware.CSRF_EXEMPT_PATHS``: a mail client
sends neither, and the token, not a cookie, is the credential, so there is no ambient authority to
forge), and both count against ``UNSUBSCRIBE_IP`` first, a bad token included. A token can only
turn a category or everything off. A good token for a deleted account answers exactly as for a
live one; every other token is the same 400. There is no ``GET`` route here.
"""

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field

from classbeat.auth.clientip import bucket_ip
from classbeat.auth.deps import request_ip, require_user
from classbeat.auth.flowtools import factory, reserve
from classbeat.auth.mailprefs import get_mail_prefs, set_mail_prefs, turn_off
from classbeat.auth.ratelimit import UNSUBSCRIBE_IP, Bucket
from classbeat.auth.unsubscribe_token import CATEGORIES, TOKEN_MAX, verify_unsubscribe_token
from classbeat.context import UserContext
from classbeat.db import system_session

router = APIRouter(prefix="/api/mail", tags=["mail"])

Category = Literal["welcome", "account_activity", "class_ready"]


class PreferencesIn(BaseModel):
    unsubscribed_all: bool
    opted_out: list[Category] = Field(max_length=len(CATEGORIES))


class PreferencesOut(BaseModel):
    unsubscribed_all: bool
    opted_out: list[str]


class TokenIn(BaseModel):
    token: str = Field(max_length=TOKEN_MAX)


class Unsubscribed(BaseModel):
    status: str = "ok"


@router.get("/preferences")
async def read_preferences(
    request: Request, ctx: Annotated[UserContext, Depends(require_user)]
) -> PreferencesOut:
    async with system_session(factory(request)) as db:
        prefs = await get_mail_prefs(db, ctx.user_id)
    if prefs is None:
        raise HTTPException(status_code=404, detail={"reason": "not_found"})
    return PreferencesOut(
        unsubscribed_all=prefs.unsubscribed_all, opted_out=sorted(prefs.opted_out)
    )


@router.put("/preferences")
async def write_preferences(
    body: PreferencesIn, request: Request, ctx: Annotated[UserContext, Depends(require_user)]
) -> PreferencesOut:
    async with system_session(factory(request)) as db:
        found = await set_mail_prefs(
            db,
            ctx.user_id,
            unsubscribed_all=body.unsubscribed_all,
            opted_out=frozenset(body.opted_out),
        )
    if not found:
        raise HTTPException(status_code=404, detail={"reason": "not_found"})
    return PreferencesOut(
        unsubscribed_all=body.unsubscribed_all, opted_out=sorted(set(body.opted_out))
    )


async def _unsubscribe(request: Request, token: str) -> Unsubscribed:
    await reserve(request, [Bucket(UNSUBSCRIBE_IP, bucket_ip(request_ip(request)))])
    verified = verify_unsubscribe_token(request.app.state.keys, token)
    if verified is None:
        raise HTTPException(status_code=400, detail={"reason": "link_invalid"})
    user_id, scope = verified
    async with system_session(factory(request)) as db:
        await turn_off(db, user_id, scope)  # a user that is gone changes nothing: same answer
    return Unsubscribed()


@router.post("/unsubscribe")
async def one_click(
    request: Request, token: Annotated[str, Query(max_length=TOKEN_MAX)] = ""
) -> Unsubscribed:
    """RFC 8058 one-click: the token is in the query, the form body is not read."""
    return await _unsubscribe(request, token)


@router.post("/unsubscribe/confirm")
async def confirm(body: TokenIn, request: Request) -> Unsubscribed:
    """The page's button: the token it read from the fragment."""
    return await _unsubscribe(request, body.token)
