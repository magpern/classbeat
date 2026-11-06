"""The mails we send (ADR 0003 section 5, ADR 0022): thin builders over ``classbeat.emails``.

Each returns a ``MailMessage`` (text and HTML parts) for the caller to send after its commit.
Links come from ``CLASSBEAT_PUBLIC_BASE_URL`` with the token in the URL fragment, never from the
Host header; the words are in ``emails/locales/en.py``.
"""

from datetime import UTC, datetime

from classbeat import emails
from classbeat.config import Settings
from classbeat.mail import MailMessage


def _now() -> datetime:
    return datetime.now(UTC)


def signup_confirmation(settings: Settings, to: str, token: str) -> MailMessage:
    return emails.render(settings, "signup_confirm", to, {"token": token})


def account_exists(settings: Settings, to: str) -> MailMessage:
    return emails.render(settings, "account_exists", to, {})


def password_reset(settings: Settings, to: str, token: str) -> MailMessage:
    return emails.render(settings, "password_reset", to, {"token": token})


def email_change_confirmation(settings: Settings, to: str, token: str) -> MailMessage:
    return emails.render(settings, "email_change_confirm", to, {"token": token})


def email_change_taken(settings: Settings, to: str) -> MailMessage:
    return emails.render(settings, "email_change_taken", to, {})


def email_changed_notice(settings: Settings, to: str) -> MailMessage:
    return emails.render(settings, "email_changed", to, {"when": _now()})


def provider_signup_confirmation(
    settings: Settings, to: str, provider: str, token: str
) -> MailMessage:
    params = {"provider": provider, "token": token}
    return emails.render(settings, "provider_signup_confirm", to, params)


def identity_linked_notice(settings: Settings, to: str, provider: str) -> MailMessage:
    return emails.render(settings, "identity_linked", to, {"provider": provider, "when": _now()})


def connections_wiped_notice(
    settings: Settings, to: str, providers: tuple[str, ...], reset_token: str | None
) -> MailMessage:
    """After ``keys wipe`` (ADR 0004 section 2); see ``emails.kinds.connections_wiped``."""
    params = {"providers": providers, "reset_token": reset_token}
    return emails.render(settings, "connections_wiped", to, params)
