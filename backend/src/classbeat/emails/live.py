"""The admin's mail settings as the send path sees them (ADR 0022 sections 2 and 6; #653).

``load`` reads the four ``app_settings`` rows (a system table the app role may read) on every
call, nothing cached, and joins them with the environment: the branding (sender name, support
address, footer) and the kinds the admin switched off. A missing, ``null`` or invalid row is the
environment's value or nothing disabled, and only an ``admin_can_disable`` kind counts as
disabled, so a stale row never silences a mail that must go out.

``send_optional`` is the path a caller without its own preference data uses until ME4: the admin
switch and the live branding apply, and the recipient is a known account (the caller only mails an
address it just authenticated), so the preferences are the defaults.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from classbeat.config import Settings
from classbeat.db import system_session
from classbeat.emails.dispatch import DEFAULT_SENDER_NAME, MailPrefs, send
from classbeat.emails.layout import Branding
from classbeat.emails.registry import REGISTRY
from classbeat.flags import core, effective
from classbeat.mail import Mailer


@dataclass(frozen=True, slots=True)
class MailConfig:
    branding: Branding
    disabled: frozenset[str]


def kind_switches() -> dict[str, bool]:
    """Kind id -> whether the admin may switch it off (the write side's allowlist)."""
    return {kind.id: kind.admin_can_disable for kind in REGISTRY.values()}


def config_from(settings: Settings, stored: Mapping[str, Any]) -> MailConfig:
    name = core.mail_sender_name_setting(stored.get("mail_sender_name"))
    address = core.mail_support_email_setting(stored.get("mail_support_email"))
    footer = core.mail_footer_setting(stored.get("mail_footer"))
    can_disable = frozenset(k for k, ok in kind_switches().items() if ok)
    return MailConfig(
        Branding(name or DEFAULT_SENDER_NAME, address or settings.mail_support_address, footer),
        core.mail_kinds_disabled_setting(stored.get("mail_kinds_disabled"), can_disable),
    )


async def load(db: AsyncSession, settings: Settings) -> MailConfig:
    return config_from(settings, await effective.stored_all(db))


async def _default_prefs(_recipient: str) -> MailPrefs | None:
    return MailPrefs()


async def send_optional(
    factory: async_sessionmaker[AsyncSession],
    mailer: Mailer,
    settings: Settings,
    kind_id: str,
    recipient: str,
    params: Mapping[str, Any],
) -> bool:
    """Send one mail through the gate with the live settings; ``True`` if an attempt was made."""
    async with system_session(factory) as db:
        config = await load(db, settings)
    return await send(
        mailer,
        settings,
        kind_id,
        recipient,
        params,
        lookup=_default_prefs,
        disabled=config.disabled,
        branding=config.branding,
    )
