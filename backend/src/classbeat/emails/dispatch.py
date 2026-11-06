"""Rendering and the single send path (ADR 0022 section 1).

``emails.send`` is the one way a mail leaves: admin switch, then the user's preference (essential
kinds skip both and never look up a user), then render with the live branding, then
``send_logged`` once. Fire and forget: a failure is logged by class and never undoes the change
that caused the mail.
"""

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any

from classbeat.config import Settings
from classbeat.emails.blocks import Content
from classbeat.emails.catalogue import mail_locale
from classbeat.emails.layout import Branding, Ctx, render_html, render_text
from classbeat.emails.registry import REGISTRY, Kind
from classbeat.mail import Mailer, MailMessage, send_logged

# Branded mail names the sender in From (the address is always ``smtp_sender``).
DEFAULT_SENDER_NAME = "classbeat"


@dataclass(frozen=True, slots=True)
class MailPrefs:
    """A user's mail preferences (the columns arrive with ME4)."""

    unsubscribed_all: bool = False
    opted_out: frozenset[str] = frozenset()


PrefsLookup = Callable[[str], Awaitable[MailPrefs | None]]


def branding_for(settings: Settings) -> Branding:
    """The branding from the environment; ME2 overlays the admin's settings."""
    return Branding(DEFAULT_SENDER_NAME, settings.mail_support_address)


def build(
    settings: Settings,
    kind: Kind,
    recipient: str,
    params: Mapping[str, Any],
    *,
    branding: Branding | None = None,
    unsubscribe_url: str | None = None,
) -> MailMessage:
    """Render one mail: the same blocks to a text part and an HTML part."""
    brand = branding or branding_for(settings)
    ctx = Ctx(settings.public_base_url, brand, mail_locale())
    content: Content = kind.builder(ctx, params)
    optional = not kind.essential
    return MailMessage(
        recipient,
        content.subject,
        render_text(ctx, content, optional=optional, unsubscribe_url=unsubscribe_url),
        render_html(ctx, content, optional=optional, unsubscribe_url=unsubscribe_url),
        ctx.locale,
        brand.sender_name,
        brand.support_address,
    )


def render(
    settings: Settings, kind_id: str, recipient: str, params: Mapping[str, Any]
) -> MailMessage:
    return build(settings, REGISTRY[kind_id], recipient, params)


async def send(
    mailer: Mailer,
    settings: Settings,
    kind_id: str,
    recipient: str,
    params: Mapping[str, Any],
    *,
    prefs: MailPrefs | None = None,
    lookup: PrefsLookup | None = None,
    disabled: frozenset[str] = frozenset(),
    branding: Branding | None = None,
) -> bool:
    """Send one mail through the gate; ``True`` if an attempt was made.

    ``disabled`` is the admin's switched-off kinds (ME2); an essential kind ignores it. An optional
    kind uses ``prefs`` or looks them up by recipient with ``lookup``; with no account found it is
    not sent.
    """
    kind = REGISTRY[kind_id]
    if not kind.essential:
        if kind.id in disabled and kind.admin_can_disable:
            return False
        if kind.user_can_opt_out:
            if prefs is None and lookup is not None:
                prefs = await lookup(recipient)
            if prefs is None or prefs.unsubscribed_all or kind.category in prefs.opted_out:
                return False
    await send_logged(mailer, build(settings, kind, recipient, params, branding=branding))
    return True
