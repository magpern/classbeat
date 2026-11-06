"""The account's language (ADR 0023 sections 2 and 9; ML2, #664).

``users.locale`` is ``NULL`` (follow the browser, English mail) or a code the operator offers. The
statement lives here because guard 8 keeps the account tables in this package. The request locale
(the body's field, then ``Accept-Language``, then English) is ``classbeat.locales.request_locale``;
``for_request`` binds it to a request's settings and header.
"""

from uuid import UUID

from fastapi import Request
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from classbeat import locales
from classbeat.auth.tables import users


def for_request(request: Request, field: object) -> str:
    """The language of this request: always a code the operator offers (or ``en``)."""
    return locales.request_locale(
        request.app.state.settings, field, request.headers.get("accept-language")
    )


def for_new_account(request: Request, field: object, row_locale: str | None = None) -> str | None:
    """The language a new account (or its pending row) stores: the body's field if offered, else
    the language already on the pending row (if still offered), else the header, else ``en``.
    ``None`` while only one language is offered, so those accounts are not pinned to it."""
    settings = request.app.state.settings
    if locales.is_offered(settings, field) and isinstance(field, str):
        chosen = field
    elif row_locale is not None and locales.is_offered(settings, row_locale):
        chosen = row_locale
    else:
        chosen = for_request(request, None)
    return locales.storable_locale(settings, chosen)


async def set_user_locale(db: AsyncSession, user_id: UUID, locale: str | None) -> bool:
    """Set the user's own language (the caller checked it); ``False`` when the user is gone."""
    result = await db.execute(update(users).where(users.c.id == user_id).values(locale=locale))
    return bool(result.rowcount)  # pyright: ignore[reportAttributeAccessIssue]
