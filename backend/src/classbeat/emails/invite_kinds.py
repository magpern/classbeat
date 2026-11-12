"""The invite code mail (ADR 0033 section 5): an admin issued an invite for an invite request
and chose to mail it. Essential: there is no account yet, so no preference or admin switch applies;
it is sent only when the admin asks for it, and only when mail is configured.

Parameters: ``code`` (the invite code, shown once to the admin and written here as text; never
logged, ``MailMessage`` hides its body) and ``days`` (how long it lives). The code is a value of
the text, never a template or a link, and the layout escapes it.
"""

from classbeat.emails.blocks import Button, Content, Heading, Note, Paragraph
from classbeat.emails.kinds import Params
from classbeat.emails.layout import Ctx
from classbeat.emails.locales.en import S


def invite_code(c: Ctx, p: Params) -> Content:
    return Content(
        c.t(S.SUBJECT_INVITE_CODE),
        c.t(S.INVITE_PRE),
        (
            Heading(c.t(S.INVITE_HEADING)),
            Paragraph(c.t(S.INVITE_BODY)),
            Paragraph(str(p["code"])),
            Button(c.t(S.INVITE_BUTTON), c.url("/signup")),
            Note(c.t(S.INVITE_NOTE, days=int(p["days"]))),
        ),
        c.t(S.REASON_REQUEST),
    )
