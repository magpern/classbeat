"""The passkey notices (ADR 0019 sections 4 and 7, #772, #773): a passkey was added or removed,
or a sign-in with it was refused because its signature counter did not go up.

Parameters: ``when`` and ``label``. The label is the user's own text (1 to 64 printable
characters, ``auth.passkey_policy``); it goes in as a ``str.format`` value, never as a template,
and the layout escapes it. Never a credential id, key or anything else from the authenticator.
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
