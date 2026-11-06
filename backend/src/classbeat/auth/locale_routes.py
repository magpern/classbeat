"""``PUT /api/account/locale``: the signed-in user's own language (ADR 0023 section 2; ML2, #664).

The body is ``{"locale": "sv"}`` or ``{"locale": null}`` ("follow this browser"; mail is English).
A code the operator does not offer is refused with ``400 locale_not_offered`` and never echoed.
Only the caller's own row is written, under the usual Origin and CSRF checks. The language
reaches the page in ``GET /api/auth/session``.
"""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from classbeat import locales
from classbeat.auth.deps import require_user
from classbeat.auth.flowtools import factory
from classbeat.auth.locale import set_user_locale
from classbeat.context import UserContext
from classbeat.db import system_session

router = APIRouter(prefix="/api/account", tags=["account"])


class LocaleIn(BaseModel):
    # Any JSON value is read and checked against the offered codes below (a wrong type is the
    # same refusal as an unknown code); the key itself is required.
    locale: Any


class LocaleOut(BaseModel):
    locale: str | None


@router.put("/locale")
async def put_locale(
    body: LocaleIn, request: Request, ctx: Annotated[UserContext, Depends(require_user)]
) -> LocaleOut:
    value = body.locale
    if value is not None and not locales.is_offered(request.app.state.settings, value):
        raise HTTPException(status_code=400, detail={"reason": "locale_not_offered"})
    async with system_session(factory(request)) as db:
        found = await set_user_locale(db, ctx.user_id, value)
    if not found:
        raise HTTPException(status_code=404, detail={"reason": "not_found"})
    return LocaleOut(locale=value)
