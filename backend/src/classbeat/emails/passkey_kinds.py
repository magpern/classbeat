"""The passkey notices (ADR 0019 sections 4 and 7, #772, #773): a passkey was added or removed,
or a sign-in with it was refused because its signature counter did not go up. And the reset link
of ``admin mfa-reset`` (section 9, #775), which says that the TOTP, recovery codes and passkeys
are gone (parameter ``token``).

Parameters of the notices: ``when`` and ``label``. The label is the user's own text (1 to 64
printable characters, ``auth.passkey_policy``); it goes in as a ``str.format`` value, never as a
template, and the layout escapes it. Never a credential id, key or anything else from the
authenticator.
"""

from classbeat.emails.blocks import Button, Content, EventTime, Heading, Note, Paragraph
from classbeat.emails.kinds import Params
from classbeat.emails.layout import Ctx
from classbeat.emails.locales.en import S


def passkey_added(c: Ctx, p: Params) -> Content:
    return Content(
        c.t(S.SUBJECT_PASSKEY_ADDED),
        c.t(S.PASSKEY_ADDED_PRE),
        (
            Heading(c.t(S.PASSKEY_ADDED_HEADING)),
            Paragraph(c.t(S.PASSKEY_ADDED_BODY, label=str(p["label"]))),
            EventTime(c.t(S.PASSKEY_WHEN), p["when"]),
            Button(c.t(S.ACCOUNT_BUTTON), c.url("/account")),
            Note(c.t(S.PASSKEY_ADDED_WARN) + " " + c.t(S.CONTACT_LEAD), contact=True),
        ),
        c.t(S.REASON_SECURITY),
    )


def passkey_removed(c: Ctx, p: Params) -> Content:
    return Content(
        c.t(S.SUBJECT_PASSKEY_REMOVED),
        c.t(S.PASSKEY_REMOVED_PRE),
        (
            Heading(c.t(S.PASSKEY_REMOVED_HEADING)),
            Paragraph(c.t(S.PASSKEY_REMOVED_BODY, label=str(p["label"]))),
            EventTime(c.t(S.PASSKEY_WHEN), p["when"]),
            Button(c.t(S.ACCOUNT_BUTTON), c.url("/account")),
            Note(c.t(S.PASSKEY_REMOVED_WARN) + " " + c.t(S.CONTACT_LEAD), contact=True),
        ),
        c.t(S.REASON_SECURITY),
    )


def passkey_counter_regressed(c: Ctx, p: Params) -> Content:
    return Content(
        c.t(S.SUBJECT_PASSKEY_COUNTER),
        c.t(S.PASSKEY_COUNTER_PRE),
        (
            Heading(c.t(S.PASSKEY_COUNTER_HEADING)),
            Paragraph(c.t(S.PASSKEY_COUNTER_BODY, label=str(p["label"]))),
            EventTime(c.t(S.PASSKEY_WHEN), p["when"]),
            Button(c.t(S.ACCOUNT_BUTTON), c.url("/account")),
            Note(c.t(S.PASSKEY_COUNTER_WARN) + " " + c.t(S.CONTACT_LEAD), contact=True),
        ),
        c.t(S.REASON_SECURITY),
    )


def mfa_reset_link(c: Ctx, p: Params) -> Content:
    """The password-reset link with what ``admin mfa-reset`` removed, and a warning instead of
    "ignore this mail": the account was already changed, whoever asked."""
    return Content(
        c.t(S.SUBJECT_PASSWORD_RESET),
        c.t(S.RESET_PRE),
        (
            Heading(c.t(S.RESET_HEADING)),
            Paragraph(c.t(S.PASSKEY_MFA_RESET_NOTE)),
            Paragraph(c.t(S.RESET_BODY)),
            Button(c.t(S.RESET_BUTTON), c.url("/reset", p["token"])),
            Note(c.t(S.PASSKEY_MFA_RESET_WARN) + " " + c.t(S.CONTACT_LEAD), contact=True),
        ),
        c.t(S.REASON_SECURITY),
    )
