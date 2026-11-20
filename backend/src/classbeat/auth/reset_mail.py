"""The password-reset mail (ADR 0003 section 5): issue a ``reset_password`` token and build the
mail, for the reset request and for ``admin mfa-reset``'s sender. Split out of ``reset_routes`` on
touch (#1265).
"""

from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from classbeat.auth import locale as account_locale
from classbeat.auth import mails
from classbeat.auth.hooks import ResetLinkSender
from classbeat.auth.tokens import RESET_LIFETIME, issue_token
from classbeat.config import Settings
from classbeat.db import system_session
from classbeat.mail import Mailer

RESET_PURPOSE = "reset_password"


async def reset_mail(
    settings: Settings, db: AsyncSession, user_id: UUID, email: str, *, mfa_reset: bool = False
):
    """Issue a reset token for the user and build the mail."""
    token = await issue_token(db, RESET_PURPOSE, user_id, RESET_LIFETIME)
    language = await account_locale.mail_locale_of(db, settings, user_id)
    build = mails.mfa_reset_link if mfa_reset else mails.password_reset
    return build(settings, email, token, language)


def make_reset_link_sender(
    settings: Settings, session_factory: async_sessionmaker[AsyncSession], mail: Mailer
) -> ResetLinkSender:
    """The sender ``auth.register_reset_link_sender`` takes: ``admin mfa-reset`` mails the
    user a reset link (awaited: the CLI has no response to send first) that says what the
    reset removed (ADR 0019 section 9)."""

    async def send(user_id: UUID, email: str) -> None:
        async with system_session(session_factory) as db:
            message = await reset_mail(settings, db, user_id, email, mfa_reset=True)
        await mail.send(message)

    return send
