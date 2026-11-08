"""The mails we send (ADR 0003 section 5, ADR 0022): thin builders over ``classbeat.emails``.

Each returns a ``MailMessage`` (text and HTML parts) for the caller to send after its commit.
Links come from ``CLASSBEAT_PUBLIC_BASE_URL`` with the token in the URL fragment, never from the
Host header; the words are in ``emails/locales/`` (``locale``: the language the caller decided,
else English).
"""

from datetime import UTC, datetime

from classbeat import emails
from classbeat.config import Settings
from classbeat.mail import MailMessage


def _now() -> datetime:
    return datetime.now(UTC)


def signup_confirmation(
    settings: Settings, to: str, token: str, locale: str | None = None
) -> MailMessage:
    return emails.render(settings, "signup_confirm", to, {"token": token}, locale=locale)


def account_exists(settings: Settings, to: str, locale: str | None = None) -> MailMessage:
    return emails.render(settings, "account_exists", to, {}, locale=locale)


def password_reset(
    settings: Settings, to: str, token: str, locale: str | None = None
) -> MailMessage:
    return emails.render(settings, "password_reset", to, {"token": token}, locale=locale)


def mfa_reset_link(
    settings: Settings, to: str, token: str, locale: str | None = None
) -> MailMessage:
    """The reset link of ``admin mfa-reset``, saying it removed the TOTP, recovery codes and
    passkeys (ADR 0019 section 9)."""
    return emails.render(settings, "mfa_reset_link", to, {"token": token}, locale=locale)


def email_change_confirmation(
    settings: Settings, to: str, token: str, locale: str | None = None
) -> MailMessage:
    return emails.render(settings, "email_change_confirm", to, {"token": token}, locale=locale)


def email_change_taken(settings: Settings, to: str, locale: str | None = None) -> MailMessage:
    return emails.render(settings, "email_change_taken", to, {}, locale=locale)


def email_changed_notice(settings: Settings, to: str, locale: str | None = None) -> MailMessage:
    return emails.render(settings, "email_changed", to, {"when": _now()}, locale=locale)


def provider_signup_confirmation(
    settings: Settings, to: str, provider: str, token: str, locale: str | None = None
) -> MailMessage:
    params = {"provider": provider, "token": token}
    return emails.render(settings, "provider_signup_confirm", to, params, locale=locale)


def connections_wiped_notice(
    settings: Settings,
    to: str,
    providers: tuple[str, ...],
    reset_token: str | None,
    locale: str | None = None,
) -> MailMessage:
    """After ``keys wipe`` (ADR 0004 section 2); see ``emails.kinds.connections_wiped``."""
    params = {"providers": providers, "reset_token": reset_token}
    return emails.render(settings, "connections_wiped", to, params, locale=locale)
