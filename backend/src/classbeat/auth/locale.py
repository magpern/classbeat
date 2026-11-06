"""The account's language (ADR 0023 sections 2 and 9; ML2, #664).

``users.locale`` is ``NULL`` (follow the browser, English mail) or a code the operator offers. The
statement lives here because guard 8 keeps the account tables in this package. The request locale
(the body's field, then ``Accept-Language``, then English) is ``classbeat.locales.request_locale``;
``for_request`` binds it to a request's settings and header.
"""

from uuid import UUID

from fastapi import Request
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from classbeat import locales
from classbeat.auth.tables import users
from classbeat.config import Settings
from classbeat.emails.catalogue import mail_locale


def for_request(request: Request, field: object) -> str:
    """The language of this request: always a code the operator offers (or ``en``)."""
    return locales.request_locale(
        request.app.state.settings, field, request.headers.get("accept-language")
    )


def chosen(request: Request, field: object, row_locale: str | None = None) -> str:
    """The language a new account or its mail uses: the body's field if offered, else the language
    already on the pending row (if still offered), else the header, else ``en``. Never ``None``;
    ``for_new_account`` is the storable form."""
    settings = request.app.state.settings
    found = locales.offered_value(settings, field) or locales.offered_value(settings, row_locale)
    return found if found is not None else for_request(request, None)


def for_new_account(request: Request, field: object, row_locale: str | None = None) -> str | None:
    """What a new account (or its pending row) stores: ``chosen``, or ``None`` while only one
    language is offered, so those accounts are not pinned to it."""
    return locales.storable_locale(request.app.state.settings, chosen(request, field, row_locale))


async def set_user_locale(db: AsyncSession, user_id: UUID, locale: str | None) -> bool:
    """Set the user's own language (the caller checked it); ``False`` when the user is gone."""
    result = await db.execute(update(users).where(users.c.id == user_id).values(locale=locale))
    return bool(result.rowcount)  # pyright: ignore[reportAttributeAccessIssue]


async def mail_locale_of(db: AsyncSession, settings: Settings, user_id: UUID) -> str:
    """The language of the mail an account route owes its user: their stored ``users.locale``
    when the operator still offers it, else ``en`` (no browser at send time, ADR 0023 section 2)."""
    stored = (
        await db.execute(select(users.c.locale).where(users.c.id == user_id))
    ).scalar_one_or_none()
    return mail_locale(stored, settings)
