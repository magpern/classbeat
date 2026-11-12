"""A provider sign-up's account (ADR 0003 section 5, the "new identity" rows of the callback
table), shared by the callback (``oauth_outcomes``) and the hand-off confirmation
(``oauth_handoff``): one savepoint that consumes the invite, creates the verified user and the
identity, and grants the invite's tier (ADR 0033, ``invite_tier``), or leaves nothing behind.
"""

from uuid import UUID

from sqlalchemy import insert, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from classbeat.auth.accounts import create_user, use_invite
from classbeat.auth.invite_tier import grant_invite_tier
from classbeat.auth.tables import identities, invites
from classbeat.config import Settings


class Refused(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


async def consume_invite(db: AsyncSession, invite_id: UUID | None) -> None:
    """The conditional ``UPDATE`` of a provider sign-up. An invite the flow or hand-off carried
    is consumed whatever the sign-up mode is by then, so it cannot be used again. Raises
    ``Refused('invite_invalid')`` when the invite is gone; the caller's savepoint rolls
    everything back."""
    if invite_id is None:
        return
    if not await use_invite(db, invite_id):
        raise Refused("invite_invalid")


async def create_provider_account(
    db: AsyncSession,
    settings: Settings,
    *,
    invite_id: UUID | None,
    email: str,
    provider: str,
    subject: str,
    locale: str | None = None,
) -> UUID:
    """One savepoint: consume the invite, create the user (verified) and the identity, grant
    the invite's tier. Raises ``Refused`` with ``invite_invalid`` (the invite or its tier's code
    is gone) or ``account_exists``; nothing is left behind."""
    try:
        async with db.begin_nested():
            await consume_invite(db, invite_id)
            user_id = await create_user(db, email, locale=locale)
            await db.execute(
                insert(identities).values(
                    user_id=user_id, provider=provider, subject=subject, email_at_link=email
                )
            )
            if invite_id is not None:
                await db.execute(
                    update(invites).where(invites.c.id == invite_id).values(used_by=user_id)
                )
            if not await grant_invite_tier(db, invite_id, user_id):
                raise Refused("invite_invalid")
    except IntegrityError:
        raise Refused("account_exists") from None  # never chained: DETAIL holds the address
    return user_id
