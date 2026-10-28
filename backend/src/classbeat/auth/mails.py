"""The texts of the mails we send (ADR 0003 section 5). Plain text; links come from
``CLASSBEAT_PUBLIC_BASE_URL`` with the token in the URL fragment, never from the Host header."""

from classbeat.config import Settings
from classbeat.mail import MailMessage


def link(settings: Settings, path: str, token: str) -> str:
    return f"{settings.public_base_url}{path}#token={token}"


def signup_confirmation(settings: Settings, to: str, token: str) -> MailMessage:
    return MailMessage(
        to,
        "Confirm your classbeat account",
        "Someone asked to create a classbeat account with this address.\n\n"
        "To finish, open this link (valid for 24 hours) and type the password you chose:\n"
        f"{link(settings, '/signup/confirm', token)}\n\n"
        "If this wasn't you, ignore this mail. No account is created.\n",
    )


def account_exists(settings: Settings, to: str) -> MailMessage:
    return MailMessage(
        to,
        "You already have a classbeat account",
        "Someone asked to create a classbeat account with this address, which already has one.\n\n"
        f"Sign in: {settings.public_base_url}/signin\n"
        f"Forgot your password? {settings.public_base_url}/reset\n\n"
        "If this wasn't you, ignore this mail.\n",
    )


def password_reset(settings: Settings, to: str, token: str) -> MailMessage:
    return MailMessage(
        to,
        "Reset your classbeat password",
        "To choose a new password, open this link (valid for 30 minutes):\n"
        f"{link(settings, '/reset', token)}\n\n"
        "Resetting signs you out everywhere. If this wasn't you, ignore this mail.\n",
    )


def email_change_confirmation(settings: Settings, to: str, token: str) -> MailMessage:
    return MailMessage(
        to,
        "Confirm your new classbeat email address",
        "To use this address for your classbeat account, sign in and open this link "
        f"(valid for 1 hour):\n{link(settings, '/email-change', token)}\n\n"
        "If this wasn't you, ignore this mail.\n",
    )


def email_change_taken(settings: Settings, to: str) -> MailMessage:
    return MailMessage(
        to,
        "A classbeat account already uses this address",
        "Someone asked to move a classbeat account to this address, which already belongs "
        f"to an account.\n\nSign in: {settings.public_base_url}/signin\n\n"
        "If this wasn't you, ignore this mail.\n",
    )


def email_changed_notice(to: str) -> MailMessage:
    return MailMessage(
        to,
        "Your classbeat email address was changed",
        "The email address of your classbeat account was changed. This is the old address.\n"
        "If this wasn't you, contact the operator.\n",
    )
