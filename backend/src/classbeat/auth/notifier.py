"""``SecurityNotifier`` (``classbeat.auth.notices``) implemented as mail (ADR 0003 section 7).

The callers (#39's routes) run the notices as background tasks and log a failure by its class
only. A disabled kind sends nothing.
"""

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from classbeat.config import Settings
from classbeat.emails import live
from classbeat.mail import Mailer


class MailNotifier:
    """Both notices are optional kinds: the admin's switch and the live branding apply (#653)."""

    def __init__(
        self,
        mailer: Mailer,
        settings: Settings,
        factory: async_sessionmaker[AsyncSession],
    ) -> None:
        self._mailer = mailer
        self._settings = settings
        self._factory = factory

    async def _notice(self, kind_id: str, email: str) -> None:
        await live.send_optional(
            self._factory, self._mailer, self._settings, kind_id, email, {"when": datetime.now(UTC)}
        )

    async def totp_enrolled(self, user_id: UUID, email: str) -> None:
        await self._notice("totp_enrolled", email)

    async def mfa_failures(self, user_id: UUID, email: str) -> None:
        await self._notice("mfa_failures", email)
