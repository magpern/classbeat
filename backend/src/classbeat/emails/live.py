"""The admin's mail settings as the send path sees them (ADR 0022 sections 2 and 6; #653).

``load`` reads the four ``app_settings`` rows (a system table the app role may read) on every
call, nothing cached, and joins them with the environment: the branding (sender name, support
address, footer) and the kinds the admin switched off. A missing, ``null`` or invalid row is the
environment's value or nothing disabled, and only an ``admin_can_disable`` kind counts as
disabled, so a stale row never silences a mail that must go out.

``send_optional`` is the path of the background notices: the admin switch and the live branding
apply, and the user's preferences are read by the account's id when the caller has it (so a notice
still finds its account after an address change) or else by address (``auth.mailprefs``). With no
account found the mail is not sent; otherwise it carries that user's signed unsubscribe links (ME4,
#655).
"""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from classbeat.auth import Keys, get_mail_prefs, mail_prefs_for_email
from classbeat.config import Settings
from classbeat.db import system_session
from classbeat.emailnorm import normalize_email
from classbeat.emails.dispatch import DEFAULT_SENDER_NAME, MailPrefs, PrefsLookup, send
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


def _lookup(factory: async_sessionmaker[AsyncSession], user_id: UUID | None) -> PrefsLookup:
    async def lookup(recipient: str) -> MailPrefs | None:
        async with system_session(factory) as db:
            if user_id is not None:
                row = await get_mail_prefs(db, user_id)
            else:
                row = await mail_prefs_for_email(db, normalize_email(recipient))
        if row is None:
            return None
        return MailPrefs(row.unsubscribed_all, row.opted_out, row.user_id, row.locale)

    return lookup


async def send_optional(
    factory: async_sessionmaker[AsyncSession],
    mailer: Mailer,
    settings: Settings,
    kind_id: str,
    recipient: str,
    params: Mapping[str, Any],
    *,
    prefs: MailPrefs | None = None,
    user_id: UUID | None = None,
    locale: str | None = None,
) -> bool:
    """Send one mail through the gate with the live settings; ``True`` if an attempt was made.

    ``prefs`` are the recipient's preferences when the caller holds them (account deleted: read
    before the cascade). Without them the gate looks them up by ``user_id`` (the recipient's
    account, when the caller knows it) or else by recipient."""
    async with system_session(factory) as db:
        config = await load(db, settings)
    return await send(
        mailer,
        settings,
        kind_id,
        recipient,
        params,
        prefs=prefs,
        locale=locale,
        lookup=_lookup(factory, user_id),
        disabled=config.disabled,
        branding=config.branding,
        keys=Keys.from_settings(settings) if settings.app_secret_key is not None else None,
    )
