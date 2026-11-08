"""Rendering and the single send path (ADR 0022 section 1).

``emails.send`` is the one way a mail leaves: admin switch, then the user's preference (essential
kinds skip both and never look up a user), then render with the live branding, then
``send_logged`` once. Fire and forget: a failure is logged by class and never undoes the change
that caused the mail.
"""

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from classbeat.auth import UNSUBSCRIBE_ALL, Keys, issue_unsubscribe_token
from classbeat.config import Settings
from classbeat.emails.blocks import Content
from classbeat.emails.catalogue import DEFAULT as DEFAULT_LOCALE
from classbeat.emails.catalogue import known, mail_locale
from classbeat.emails.layout import Branding, Ctx, render_html, render_text
from classbeat.emails.registry import REGISTRY, Kind
from classbeat.mail import Mailer, MailMessage, send_logged

# Branded mail names the sender in From (the address is always ``smtp_sender``).
DEFAULT_SENDER_NAME = "classbeat"


@dataclass(frozen=True, slots=True)
class MailPrefs:
    """A user's mail preferences (``users.mail_unsubscribed_all`` and ``mail_opted_out``, ME4)."""

    unsubscribed_all: bool = False
    opted_out: frozenset[str] = frozenset()
    user_id: UUID | None = None  # the account, for the signed unsubscribe link
    locale: str | None = None  # users.locale; None sends English (no browser at send time)


PrefsLookup = Callable[[str], Awaitable[MailPrefs | None]]


@dataclass(frozen=True, slots=True)
class UnsubscribeLinks:
    """The page link (fragment: the category token and the all-optional token) and the RFC 8058
    one-click URL (query: the category token)."""

    page: str
    one_click: str


def unsubscribe_links(
    settings: Settings, keys: Keys, user_id: UUID, category: str, locale: str = DEFAULT_LOCALE
) -> UnsubscribeLinks:
    """The page link is in the recipient's language (``/{locale}/unsubscribe``, ADR 0027 section
    7); the one-click URL is an API route a mail client POSTs to and is never prefixed."""
    base = settings.public_base_url.rstrip("/")
    each, everything = (
        issue_unsubscribe_token(keys, user_id, category),
        issue_unsubscribe_token(keys, user_id, UNSUBSCRIBE_ALL),
    )
    return UnsubscribeLinks(
        f"{base}/{known(locale)}/unsubscribe#token={each}&all={everything}",
        f"{base}/api/mail/unsubscribe?token={each}",
    )


def sample_unsubscribe_links(settings: Settings, locale: str = DEFAULT_LOCALE) -> UnsubscribeLinks:
    """Placeholders for the previews and golden files: the shape, no signature."""
    base = settings.public_base_url.rstrip("/")
    return UnsubscribeLinks(
        f"{base}/{known(locale)}/unsubscribe#token=SAMPLE&all=SAMPLE",
        f"{base}/api/mail/unsubscribe?token=SAMPLE",
    )


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
    unsubscribe: UnsubscribeLinks | None = None,
    locale: str | None = None,
) -> MailMessage:
    """Render one mail: the same blocks to a text part and an HTML part.

    ``locale`` is the language the caller decided (a request, a flow, a stored code through
    ``send``); without it the mail is English."""
    brand = branding or branding_for(settings)
    ctx = Ctx(settings.public_base_url, brand, known(locale))
    content: Content = kind.builder(ctx, params)
    optional = not kind.essential
    page = unsubscribe.page if unsubscribe else None
    return MailMessage(
        recipient,
        content.subject,
        render_text(ctx, content, optional=optional, unsubscribe_url=page),
        render_html(ctx, content, optional=optional, unsubscribe_url=page),
        ctx.locale,
        brand.sender_name,
        brand.support_address,
        unsubscribe.one_click if unsubscribe and optional else None,
    )


def render(
    settings: Settings,
    kind_id: str,
    recipient: str,
    params: Mapping[str, Any],
    *,
    locale: str | None = None,
) -> MailMessage:
    return build(settings, REGISTRY[kind_id], recipient, params, locale=locale)


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
    keys: Keys | None = None,
    locale: str | None = None,
) -> bool:
    """Send one mail through the gate; ``True`` if an attempt was made.

    ``disabled`` is the admin's switched-off kinds (ME2); an essential kind ignores it. An optional
    kind uses ``prefs`` or looks them up by recipient with ``lookup``; with no account found it is
    not sent. With ``keys`` and a known account the mail carries the signed unsubscribe links.
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
    # A language handed in (an OAuth flow or hand-off row, a request) can be older than the
    # operator's setting: it is checked against the offered languages here, not only against the
    # catalogues. An essential kind still reads no user.
    if locale is not None:
        locale = mail_locale(locale, settings)
    elif prefs is not None:
        locale = mail_locale(prefs.locale, settings)
    links = None
    if not kind.essential and prefs is not None and prefs.user_id is not None and keys is not None:
        links = unsubscribe_links(settings, keys, prefs.user_id, kind.category.value, known(locale))
    await send_logged(
        mailer,
        build(
            settings, kind, recipient, params, branding=branding, unsubscribe=links, locale=locale
        ),
    )
    return True
