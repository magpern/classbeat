"""A user's mail preferences (ADR 0022 section 5; ME4 #655).

Two columns on ``users`` (guard 8 keeps the statements here): ``mail_unsubscribed_all`` and
``mail_opted_out`` (the switched-off categories). Essential kinds ignore both, in the send path.
``turn_off`` is what the signed unsubscribe link may do, and it can only add: one atomic
``UPDATE`` that never removes an entry and never touches the other column.
"""

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import Text, any_, case, func, literal, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from classbeat.auth.tables import users
from classbeat.auth.unsubscribe_token import CATEGORIES, UNSUBSCRIBE_ALL


@dataclass(frozen=True, slots=True)
class MailPreferences:
    user_id: UUID
    unsubscribed_all: bool
    opted_out: frozenset[str]
    locale: str | None = None  # users.locale; the language of the account's mail


_COLUMNS = (
    users.c.id,
    users.c.mail_unsubscribed_all,
    users.c.mail_opted_out,
    users.c.locale,
)


def _prefs(
    user_id: UUID, all_off: bool, opted_out: list[str], locale: str | None
) -> MailPreferences:
    return MailPreferences(user_id, all_off, frozenset(opted_out), locale)


async def get_mail_prefs(db: AsyncSession, user_id: UUID) -> MailPreferences | None:
    row = (await db.execute(select(*_COLUMNS).where(users.c.id == user_id))).first()
    return None if row is None else _prefs(*row)


async def mail_prefs_for_email(db: AsyncSession, email_normalized: str) -> MailPreferences | None:
    """The preferences of the account with this address, if there is one."""
    row = (
        await db.execute(select(*_COLUMNS).where(users.c.email_normalized == email_normalized))
    ).first()
    return None if row is None else _prefs(*row)


async def set_mail_prefs(
    db: AsyncSession, user_id: UUID, *, unsubscribed_all: bool, opted_out: frozenset[str]
) -> bool:
    """The signed-in user's own choice, both ways. Unknown categories are the caller's to refuse
    (the table's CHECK is the backstop)."""
    result = await db.execute(
        update(users)
        .where(users.c.id == user_id)
        .values(mail_unsubscribed_all=unsubscribed_all, mail_opted_out=sorted(opted_out))
    )
    return bool(result.rowcount)  # pyright: ignore[reportAttributeAccessIssue]


async def turn_off(db: AsyncSession, user_id: UUID, scope: str) -> None:
    """Add ``scope`` (a category or ``all``) to what the user switched off; idempotent, and a
    user that no longer exists changes nothing."""
    if scope == UNSUBSCRIBE_ALL:
        values = {"mail_unsubscribed_all": True}
    elif scope in CATEGORIES:
        column = users.c.mail_opted_out
        value = literal(scope, Text)
        values = {
            "mail_opted_out": case(
                (value == any_(column), column), else_=func.array_append(column, value)
            )
        }
    else:
        raise ValueError("unsubscribe scope is not switchable")
    await db.execute(update(users).where(users.c.id == user_id).values(**values))
