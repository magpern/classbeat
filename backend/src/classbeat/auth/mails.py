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


def totp_enrolled_notice(to: str) -> MailMessage:
    return MailMessage(
        to,
        "Two-factor authentication was set up on your classbeat account",
        "An authenticator app was enrolled (or replaced) on your classbeat account.\n"
        "If this wasn't you, reset your password and contact the operator.\n",
    )


def mfa_failures_notice(to: str) -> MailMessage:
    return MailMessage(
        to,
        "Failed two-factor attempts on your classbeat account",
        "There were five failed two-factor attempts on your classbeat account within an hour.\n"
        "If this wasn't you, change your password.\n",
    )


def provider_signup_confirmation(
    settings: Settings, to: str, provider: str, token: str
) -> MailMessage:
    return MailMessage(
        to,
        "Confirm your classbeat account",
        f"Someone signed in with {provider.title()} and asked to create a classbeat account "
        "with this address.\n\n"
        "To finish, open this link in the same browser (valid for 24 hours):\n"
        f"{link(settings, '/signup/confirm', token)}\n\n"
        "If this wasn't you, ignore this mail. No account is created.\n",
    )


def identity_linked_notice(to: str, provider: str) -> MailMessage:
    return MailMessage(
        to,
        "A sign-in method was added to your classbeat account",
        f"{provider.title()} was linked to your classbeat account as a way to sign in.\n"
        "If this wasn't you, sign in, remove it from your account page and change your "
        "password.\n",
    )


_PROVIDER_NAMES = {
    "mowl": "Intelligent Cycling (MOWL)",
    "spotify": "Spotify",
    "swedebeat": "Swedebeat",
}
_PROVIDER_STEPS = {
    "mowl": "Intelligent Cycling (MOWL): end your other sessions or change your password there. "
    "MOWL tokens cannot be revoked and may stay valid until they expire.",
    "spotify": "Spotify: remove classbeat's access (your own developer app) in your Spotify "
    "account settings.",
    "swedebeat": "Swedebeat: change your Swedebeat password.",
}


def connections_wiped_notice(
    settings: Settings, to: str, providers: tuple[str, ...], reset_token: str | None
) -> MailMessage:
    """After ``keys wipe`` (ADR 0004 section 2): which providers were affected, that classbeat
    removed them, what to do, and the reset link if the TOTP was reset. No token, id or account
    detail (the reset link carries only its own single-use token)."""
    lines = [
        "As a precaution after a security incident, classbeat removed the stored sign-ins of "
        "your account.",
        "",
    ]
    if providers:
        names = ", ".join(_PROVIDER_NAMES.get(p, p) for p in providers)
        lines += [
            f"Removed connections: {names}. Connect them again from your account page.",
            "",
            "What to do:",
            *(f"- {_PROVIDER_STEPS[p]}" for p in providers if p in _PROVIDER_STEPS),
            "",
        ]
    if reset_token is not None:
        lines += [
            "Two-factor authentication was removed and you were signed out everywhere. Choose a "
            "new password with this link (valid for 30 minutes), then set up two-factor "
            "authentication again:",
            link(settings, "/reset", reset_token),
            "",
        ]
    lines.append("If you have questions, contact the operator.")
    return MailMessage(to, "Your classbeat connections were removed", "\n".join(lines) + "\n")
