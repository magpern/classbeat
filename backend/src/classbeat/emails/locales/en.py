"""The English catalogue: every string of every mail, subjects included (ADR 0022 section 2).

Ids are an ``Enum`` so a typo is an error, not a missing string. Placeholders are ``str.format``
names. Nothing here is markup: the layout escapes. Multilingual mail (milestone ML) adds a
catalogue per locale next to this one and changes ``mail_locale`` only.
"""

from datetime import UTC, datetime
from enum import Enum, auto


class S(Enum):
    # Subjects (unchanged from the plain-text mails).
    SUBJECT_SIGNUP_CONFIRM = auto()
    SUBJECT_ACCOUNT_EXISTS = auto()
    SUBJECT_PASSWORD_RESET = auto()
    SUBJECT_EMAIL_CHANGE_CONFIRM = auto()
    SUBJECT_EMAIL_CHANGE_TAKEN = auto()
    SUBJECT_EMAIL_CHANGED = auto()
    SUBJECT_TOTP_ENROLLED = auto()
    SUBJECT_MFA_FAILURES = auto()
    SUBJECT_IDENTITY_LINKED = auto()
    SUBJECT_CONNECTIONS_WIPED = auto()
    SUBJECT_WELCOME = auto()
    SUBJECT_PASSWORD_CHANGED = auto()
    SUBJECT_ACCOUNT_DELETED = auto()
    SUBJECT_IDENTITY_UNLINKED = auto()
    SUBJECT_TOTP_REMOVED = auto()
    # Footer.
    FOOTER_SITE = auto()
    FOOTER_SUPPORT = auto()
    FOOTER_UNSUBSCRIBE = auto()
    CONTACT_LEAD = auto()
    # Why you got this.
    REASON_REQUEST = auto()
    REASON_SECURITY = auto()
    # Sign-up confirmation (also the provider variant).
    SIGNUP_HEADING = auto()
    SIGNUP_PRE = auto()
    SIGNUP_BODY = auto()
    SIGNUP_BUTTON = auto()
    SIGNUP_IGNORE = auto()
    PROVIDER_SIGNUP_PRE = auto()
    PROVIDER_SIGNUP_BODY = auto()
    # Account exists.
    EXISTS_HEADING = auto()
    EXISTS_PRE = auto()
    EXISTS_BODY = auto()
    EXISTS_SIGNIN = auto()
    EXISTS_FORGOT = auto()
    EXISTS_IGNORE = auto()
    # Password reset.
    RESET_HEADING = auto()
    RESET_PRE = auto()
    RESET_BODY = auto()
    RESET_BUTTON = auto()
    RESET_IGNORE = auto()
    # Email change.
    CHANGE_HEADING = auto()
    CHANGE_PRE = auto()
    CHANGE_BODY = auto()
    CHANGE_BUTTON = auto()
    CHANGE_IGNORE = auto()
    TAKEN_HEADING = auto()
    TAKEN_PRE = auto()
    TAKEN_BODY = auto()
    TAKEN_SIGNIN = auto()
    TAKEN_IGNORE = auto()
    CHANGED_HEADING = auto()
    CHANGED_PRE = auto()
    CHANGED_BODY = auto()
    CHANGED_WHEN = auto()
    CHANGED_WARN = auto()
    # Security notices.
    ACCOUNT_BUTTON = auto()
    TOTP_HEADING = auto()
    TOTP_PRE = auto()
    TOTP_BODY = auto()
    TOTP_WHEN = auto()
    TOTP_WARN = auto()
    MFA_HEADING = auto()
    MFA_PRE = auto()
    MFA_BODY = auto()
    MFA_WHEN = auto()
    MFA_WARN = auto()
    LINKED_HEADING = auto()
    LINKED_PRE = auto()
    LINKED_BODY = auto()
    LINKED_WHEN = auto()
    LINKED_WARN = auto()
    # New account notices (ME3).
    WELCOME_HEADING = auto()
    WELCOME_PRE = auto()
    WELCOME_BODY = auto()
    WELCOME_PROVIDER = auto()
    WELCOME_BUTTON = auto()
    WELCOME_REASON = auto()
    PWCHANGED_HEADING = auto()
    PWCHANGED_PRE = auto()
    PWCHANGED_BODY = auto()
    PWCHANGED_WHEN = auto()
    PWCHANGED_WARN = auto()
    DELETED_HEADING = auto()
    DELETED_PRE = auto()
    DELETED_BODY = auto()
    DELETED_WHEN = auto()
    DELETED_WARN = auto()
    UNLINKED_HEADING = auto()
    UNLINKED_PRE = auto()
    UNLINKED_BODY = auto()
    UNLINKED_WHEN = auto()
    UNLINKED_WARN = auto()
    TOTPOFF_HEADING = auto()
    TOTPOFF_PRE = auto()
    TOTPOFF_BODY = auto()
    TOTPOFF_WHEN = auto()
    TOTPOFF_WARN = auto()
    # Connections wiped (incident).
    WIPED_HEADING = auto()
    WIPED_PRE = auto()
    WIPED_BODY = auto()
    WIPED_REMOVED = auto()
    WIPED_TODO = auto()
    WIPED_STEP_MOWL = auto()
    WIPED_STEP_SPOTIFY = auto()
    WIPED_STEP_SWEDEBEAT = auto()
    WIPED_RESET = auto()
    WIPED_RESET_BUTTON = auto()
    WIPED_QUESTIONS = auto()
    PROVIDER_MOWL = auto()
    PROVIDER_SPOTIFY = auto()
    PROVIDER_SWEDEBEAT = auto()


CATALOGUE: dict[S, str] = {
    S.SUBJECT_SIGNUP_CONFIRM: "Confirm your classbeat account",
    S.SUBJECT_ACCOUNT_EXISTS: "You already have a classbeat account",
    S.SUBJECT_PASSWORD_RESET: "Reset your classbeat password",
    S.SUBJECT_EMAIL_CHANGE_CONFIRM: "Confirm your new classbeat email address",
    S.SUBJECT_EMAIL_CHANGE_TAKEN: "A classbeat account already uses this address",
    S.SUBJECT_EMAIL_CHANGED: "Your classbeat email address was changed",
    S.SUBJECT_TOTP_ENROLLED: "Two-factor authentication was set up on your classbeat account",
    S.SUBJECT_MFA_FAILURES: "Failed two-factor attempts on your classbeat account",
    S.SUBJECT_IDENTITY_LINKED: "A sign-in method was added to your classbeat account",
    S.SUBJECT_CONNECTIONS_WIPED: "Your classbeat connections were removed",
    S.SUBJECT_WELCOME: "Welcome to classbeat",
    S.SUBJECT_PASSWORD_CHANGED: "Your classbeat password was changed",
    S.SUBJECT_ACCOUNT_DELETED: "Your classbeat account was deleted",
    S.SUBJECT_IDENTITY_UNLINKED: "A sign-in method was removed from your classbeat account",
    S.SUBJECT_TOTP_REMOVED: "Two-factor authentication was turned off on your classbeat account",
    S.FOOTER_SITE: "classbeat",
    S.FOOTER_SUPPORT: "Questions? Write to",
    S.FOOTER_UNSUBSCRIBE: "Unsubscribe from these emails",
    S.CONTACT_LEAD: "Contact us at",
    S.REASON_REQUEST: "You got this because someone used this email address on classbeat.",
    S.REASON_SECURITY: (
        "You got this security notice because this is the email address of a classbeat account. "
        "These notices are sent for your protection."
    ),
    S.SIGNUP_HEADING: "Confirm your account",
    S.SIGNUP_PRE: "Finish creating your classbeat account.",
    S.SIGNUP_BODY: (
        "Someone asked to create a classbeat account with this address. To finish, open the "
        "link below (valid for 24 hours) and type the password you chose."
    ),
    S.SIGNUP_BUTTON: "Confirm account",
    S.SIGNUP_IGNORE: "If this wasn't you, ignore this mail. No account is created.",
    S.PROVIDER_SIGNUP_PRE: "Finish creating your classbeat account.",
    S.PROVIDER_SIGNUP_BODY: (
        "Someone signed in with {provider} and asked to create a classbeat account with this "
        "address. To finish, open the link below in the same browser (valid for 24 hours)."
    ),
    S.EXISTS_HEADING: "You already have an account",
    S.EXISTS_PRE: "This address already has a classbeat account.",
    S.EXISTS_BODY: (
        "Someone asked to create a classbeat account with this address, which already has one."
    ),
    S.EXISTS_SIGNIN: "Sign in",
    S.EXISTS_FORGOT: "Forgot your password?",
    S.EXISTS_IGNORE: "If this wasn't you, ignore this mail.",
    S.RESET_HEADING: "Choose a new password",
    S.RESET_PRE: "A link to choose a new classbeat password.",
    S.RESET_BODY: (
        "To choose a new password, open the link below (valid for 30 minutes). Resetting signs "
        "you out everywhere."
    ),
    S.RESET_BUTTON: "Choose a new password",
    S.RESET_IGNORE: "If this wasn't you, ignore this mail.",
    S.CHANGE_HEADING: "Confirm your new address",
    S.CHANGE_PRE: "Confirm the new email address for your classbeat account.",
    S.CHANGE_BODY: (
        "To use this address for your classbeat account, sign in and open the link below "
        "(valid for 1 hour)."
    ),
    S.CHANGE_BUTTON: "Confirm new address",
    S.CHANGE_IGNORE: "If this wasn't you, ignore this mail.",
    S.TAKEN_HEADING: "This address is already in use",
    S.TAKEN_PRE: "A classbeat account already uses this address.",
    S.TAKEN_BODY: (
        "Someone asked to move a classbeat account to this address, which already belongs to "
        "an account."
    ),
    S.TAKEN_SIGNIN: "Sign in",
    S.TAKEN_IGNORE: "If this wasn't you, ignore this mail.",
    S.CHANGED_HEADING: "Your email address was changed",
    S.CHANGED_PRE: "The email address of your classbeat account was changed.",
    S.CHANGED_BODY: (
        "The email address of your classbeat account was changed. This is the old address."
    ),
    S.CHANGED_WHEN: "When it happened",
    S.CHANGED_WARN: "If this wasn't you, tell us right away.",
    S.ACCOUNT_BUTTON: "Review your account",
    S.TOTP_HEADING: "Two-factor authentication was set up",
    S.TOTP_PRE: "An authenticator app was set up on your classbeat account.",
    S.TOTP_BODY: ("An authenticator app was enrolled (or replaced) on your classbeat account."),
    S.TOTP_WHEN: "When it happened",
    S.TOTP_WARN: "If this wasn't you, reset your password.",
    S.MFA_HEADING: "Failed two-factor attempts",
    S.MFA_PRE: "Five failed two-factor attempts on your classbeat account.",
    S.MFA_BODY: (
        "There were five failed two-factor attempts on your classbeat account within an hour."
    ),
    S.MFA_WHEN: "When we noticed",
    S.MFA_WARN: "If this wasn't you, change your password.",
    S.LINKED_HEADING: "A sign-in method was added",
    S.LINKED_PRE: "{provider} can now be used to sign in to your classbeat account.",
    S.LINKED_BODY: "{provider} was linked to your classbeat account as a way to sign in.",
    S.LINKED_WHEN: "When it happened",
    S.LINKED_WARN: (
        "If this wasn't you, sign in, remove it from your account page and change your password."
    ),
    S.WELCOME_HEADING: "Welcome to classbeat",
    S.WELCOME_PRE: "Your classbeat account is ready.",
    S.WELCOME_BODY: (
        "Describe a class and classbeat picks the songs, designs it and saves it to your "
        "Intelligent Cycling account."
    ),
    S.WELCOME_PROVIDER: "You can sign in with {provider}.",
    S.WELCOME_BUTTON: "Open classbeat",
    S.WELCOME_REASON: (
        "You got this because a classbeat account was just created with this address."
    ),
    S.PWCHANGED_HEADING: "Your password was changed",
    S.PWCHANGED_PRE: "The password of your classbeat account was changed.",
    S.PWCHANGED_BODY: (
        "The password of your classbeat account was changed, and your other sessions were "
        "signed out."
    ),
    S.PWCHANGED_WHEN: "When it happened",
    S.PWCHANGED_WARN: "If this wasn't you, reset your password right away.",
    S.DELETED_HEADING: "Your account was deleted",
    S.DELETED_PRE: "Your classbeat account and its data were deleted.",
    S.DELETED_BODY: (
        "Your classbeat account and the data stored with it were deleted. Nothing more is kept "
        "under this address."
    ),
    S.DELETED_WHEN: "When it happened",
    S.DELETED_WARN: "If this wasn't you, tell us right away.",
    S.UNLINKED_HEADING: "A sign-in method was removed",
    S.UNLINKED_PRE: "{provider} can no longer be used to sign in to your classbeat account.",
    S.UNLINKED_BODY: ("{provider} was removed from your classbeat account as a way to sign in."),
    S.UNLINKED_WHEN: "When it happened",
    S.UNLINKED_WARN: "If this wasn't you, change your password and tell us right away.",
    S.TOTPOFF_HEADING: "Two-factor authentication was turned off",
    S.TOTPOFF_PRE: "Two-factor authentication was turned off on your classbeat account.",
    S.TOTPOFF_BODY: (
        "Two-factor authentication was turned off on your classbeat account, and your other "
        "sessions were signed out."
    ),
    S.TOTPOFF_WHEN: "When it happened",
    S.TOTPOFF_WARN: "If this wasn't you, change your password and tell us right away.",
    S.WIPED_HEADING: "Your connections were removed",
    S.WIPED_PRE: "As a precaution, classbeat removed the stored sign-ins of your account.",
    S.WIPED_BODY: (
        "As a precaution after a security incident, classbeat removed the stored sign-ins of "
        "your account."
    ),
    S.WIPED_REMOVED: "Removed connections: {names}. Connect them again from your account page.",
    S.WIPED_TODO: "What to do:",
    S.WIPED_STEP_MOWL: (
        "Intelligent Cycling (MOWL): end your other sessions or change your password there. "
        "MOWL tokens cannot be revoked and may stay valid until they expire."
    ),
    S.WIPED_STEP_SPOTIFY: (
        "Spotify: remove classbeat's access (your own developer app) in your Spotify "
        "account settings."
    ),
    S.WIPED_STEP_SWEDEBEAT: "Swedebeat: change your Swedebeat password.",
    S.WIPED_RESET: (
        "Two-factor authentication was removed and you were signed out everywhere. Choose a "
        "new password with the link below (valid for 30 minutes), then set up two-factor "
        "authentication again."
    ),
    S.WIPED_RESET_BUTTON: "Choose a new password",
    S.WIPED_QUESTIONS: "If you have questions, contact us at",
    S.PROVIDER_MOWL: "Intelligent Cycling (MOWL)",
    S.PROVIDER_SPOTIFY: "Spotify",
    S.PROVIDER_SWEDEBEAT: "Swedebeat",
}


def format_when(when: datetime) -> str:
    """A time for a notice, always in UTC and always saying so."""
    return when.astimezone(UTC).strftime("%d %B %Y, %H:%M UTC").lstrip("0")
