"""The mail after an email change was reverted (ADR 0003 amendment of 2026-11-19, #1265).

Sent to the restored address once the "this wasn't me" link of the ``email_changed`` notice was
used. It says what the revert removed and carries a password-reset link (parameter ``token``),
with a warning instead of "ignore this mail": the account was already changed, whoever asked.
"""

from classbeat.emails.blocks import Button, Content, Heading, Note, Paragraph
from classbeat.emails.kinds import Params
from classbeat.emails.layout import Ctx
from classbeat.emails.locales.en import S


def email_reverted(c: Ctx, p: Params) -> Content:
    return Content(
        c.t(S.SUBJECT_EMAIL_REVERTED),
        c.t(S.REVERTED_PRE),
        (
            Heading(c.t(S.REVERTED_HEADING)),
            Paragraph(c.t(S.REVERTED_BODY)),
            Paragraph(c.t(S.REVERTED_NEXT)),
            Button(c.t(S.RESET_BUTTON), c.url("/reset", p["token"])),
            Note(c.t(S.REVERTED_WARN) + " " + c.t(S.CONTACT_LEAD), contact=True),
        ),
        c.t(S.REASON_SECURITY),
    )
