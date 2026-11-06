"""The content of each kind of mail: parameters in, blocks out (no markup, no sending).

Triggers and subjects are the ones the plain-text mails had; the catalogue holds the words.
"""

from collections.abc import Callable, Mapping
from datetime import datetime
from typing import Any

from classbeat.emails.blocks import Bullets, Button, Content, EventTime, Heading, Note, Paragraph
from classbeat.emails.layout import Ctx
from classbeat.emails.locales.en import S

Params = Mapping[str, Any]
Builder = Callable[[Ctx, Params], Content]

_PROVIDER_NAMES = {
    "mowl": S.PROVIDER_MOWL,
    "spotify": S.PROVIDER_SPOTIFY,
    "swedebeat": S.PROVIDER_SWEDEBEAT,
}
_PROVIDER_STEPS = {
    "mowl": S.WIPED_STEP_MOWL,
    "spotify": S.WIPED_STEP_SPOTIFY,
    "swedebeat": S.WIPED_STEP_SWEDEBEAT,
}


def _when(p: Params) -> datetime:
    return p["when"]


def signup_confirm(c: Ctx, p: Params) -> Content:
    return Content(
        c.t(S.SUBJECT_SIGNUP_CONFIRM),
        c.t(S.SIGNUP_PRE),
        (
            Heading(c.t(S.SIGNUP_HEADING)),
            Paragraph(c.t(S.SIGNUP_BODY)),
            Button(c.t(S.SIGNUP_BUTTON), c.url("/signup/confirm", p["token"])),
            Note(c.t(S.SIGNUP_IGNORE)),
        ),
        c.t(S.REASON_REQUEST),
    )


def provider_signup_confirm(c: Ctx, p: Params) -> Content:
    return Content(
        c.t(S.SUBJECT_SIGNUP_CONFIRM),
        c.t(S.PROVIDER_SIGNUP_PRE),
        (
            Heading(c.t(S.SIGNUP_HEADING)),
            Paragraph(c.t(S.PROVIDER_SIGNUP_BODY, provider=str(p["provider"]).title())),
            Button(c.t(S.SIGNUP_BUTTON), c.url("/signup/confirm", p["token"])),
            Note(c.t(S.SIGNUP_IGNORE)),
        ),
        c.t(S.REASON_REQUEST),
    )


def account_exists(c: Ctx, p: Params) -> Content:
    return Content(
        c.t(S.SUBJECT_ACCOUNT_EXISTS),
        c.t(S.EXISTS_PRE),
        (
            Heading(c.t(S.EXISTS_HEADING)),
            Paragraph(c.t(S.EXISTS_BODY)),
            Button(c.t(S.EXISTS_SIGNIN), c.url("/signin")),
            Button(c.t(S.EXISTS_FORGOT), c.url("/reset"), primary=False),
            Note(c.t(S.EXISTS_IGNORE)),
        ),
        c.t(S.REASON_REQUEST),
    )


def password_reset(c: Ctx, p: Params) -> Content:
    return Content(
        c.t(S.SUBJECT_PASSWORD_RESET),
        c.t(S.RESET_PRE),
        (
            Heading(c.t(S.RESET_HEADING)),
            Paragraph(c.t(S.RESET_BODY)),
            Button(c.t(S.RESET_BUTTON), c.url("/reset", p["token"])),
            Note(c.t(S.RESET_IGNORE)),
        ),
        c.t(S.REASON_REQUEST),
    )


def email_change_confirm(c: Ctx, p: Params) -> Content:
    return Content(
        c.t(S.SUBJECT_EMAIL_CHANGE_CONFIRM),
        c.t(S.CHANGE_PRE),
        (
            Heading(c.t(S.CHANGE_HEADING)),
            Paragraph(c.t(S.CHANGE_BODY)),
            Button(c.t(S.CHANGE_BUTTON), c.url("/email-change", p["token"])),
            Note(c.t(S.CHANGE_IGNORE)),
        ),
        c.t(S.REASON_REQUEST),
    )


def email_change_taken(c: Ctx, p: Params) -> Content:
    return Content(
        c.t(S.SUBJECT_EMAIL_CHANGE_TAKEN),
        c.t(S.TAKEN_PRE),
        (
            Heading(c.t(S.TAKEN_HEADING)),
            Paragraph(c.t(S.TAKEN_BODY)),
            Button(c.t(S.TAKEN_SIGNIN), c.url("/signin")),
            Note(c.t(S.TAKEN_IGNORE)),
        ),
        c.t(S.REASON_REQUEST),
    )


def email_changed(c: Ctx, p: Params) -> Content:
    return Content(
        c.t(S.SUBJECT_EMAIL_CHANGED),
        c.t(S.CHANGED_PRE),
        (
            Heading(c.t(S.CHANGED_HEADING)),
            Paragraph(c.t(S.CHANGED_BODY)),
            EventTime(c.t(S.CHANGED_WHEN), _when(p)),
            Note(c.t(S.CHANGED_WARN) + " " + c.t(S.CONTACT_LEAD), contact=True),
        ),
        c.t(S.REASON_SECURITY),
    )


def totp_enrolled(c: Ctx, p: Params) -> Content:
    return Content(
        c.t(S.SUBJECT_TOTP_ENROLLED),
        c.t(S.TOTP_PRE),
        (
            Heading(c.t(S.TOTP_HEADING)),
            Paragraph(c.t(S.TOTP_BODY)),
            EventTime(c.t(S.TOTP_WHEN), _when(p)),
            Button(c.t(S.ACCOUNT_BUTTON), c.url("/account")),
            Note(c.t(S.TOTP_WARN) + " " + c.t(S.CONTACT_LEAD), contact=True),
        ),
        c.t(S.REASON_SECURITY),
    )


def mfa_failures(c: Ctx, p: Params) -> Content:
    return Content(
        c.t(S.SUBJECT_MFA_FAILURES),
        c.t(S.MFA_PRE),
        (
            Heading(c.t(S.MFA_HEADING)),
            Paragraph(c.t(S.MFA_BODY)),
            EventTime(c.t(S.MFA_WHEN), _when(p)),
            Button(c.t(S.ACCOUNT_BUTTON), c.url("/account")),
            Note(c.t(S.MFA_WARN) + " " + c.t(S.CONTACT_LEAD), contact=True),
        ),
        c.t(S.REASON_SECURITY),
    )


def identity_linked(c: Ctx, p: Params) -> Content:
    provider = str(p["provider"]).title()
    return Content(
        c.t(S.SUBJECT_IDENTITY_LINKED),
        c.t(S.LINKED_PRE, provider=provider),
        (
            Heading(c.t(S.LINKED_HEADING)),
            Paragraph(c.t(S.LINKED_BODY, provider=provider)),
            EventTime(c.t(S.LINKED_WHEN), _when(p)),
            Button(c.t(S.ACCOUNT_BUTTON), c.url("/account")),
            Note(c.t(S.LINKED_WARN) + " " + c.t(S.CONTACT_LEAD), contact=True),
        ),
        c.t(S.REASON_SECURITY),
    )


def welcome(c: Ctx, p: Params) -> Content:
    """After sign-up confirmation. On OAuth account creation ``provider`` is set and the mail
    names it; that case replaces the old "sign-in method added" notice (ADR 0022 section 7)."""
    provider = p.get("provider")
    blocks: list[Any] = [Heading(c.t(S.WELCOME_HEADING)), Paragraph(c.t(S.WELCOME_BODY))]
    if provider:
        blocks.append(Paragraph(c.t(S.WELCOME_PROVIDER, provider=str(provider).title())))
    blocks.append(Button(c.t(S.WELCOME_BUTTON), c.url("/")))
    return Content(c.t(S.SUBJECT_WELCOME), c.t(S.WELCOME_PRE), tuple(blocks), c.t(S.WELCOME_REASON))


def password_changed(c: Ctx, p: Params) -> Content:
    return Content(
        c.t(S.SUBJECT_PASSWORD_CHANGED),
        c.t(S.PWCHANGED_PRE),
        (
            Heading(c.t(S.PWCHANGED_HEADING)),
            Paragraph(c.t(S.PWCHANGED_BODY)),
            EventTime(c.t(S.PWCHANGED_WHEN), _when(p)),
            Button(c.t(S.ACCOUNT_BUTTON), c.url("/account")),
            Note(c.t(S.PWCHANGED_WARN) + " " + c.t(S.CONTACT_LEAD), contact=True),
        ),
        c.t(S.REASON_SECURITY),
    )


def account_deleted(c: Ctx, p: Params) -> Content:
    """No link to the account: it is gone."""
    return Content(
        c.t(S.SUBJECT_ACCOUNT_DELETED),
        c.t(S.DELETED_PRE),
        (
            Heading(c.t(S.DELETED_HEADING)),
            Paragraph(c.t(S.DELETED_BODY)),
            EventTime(c.t(S.DELETED_WHEN), _when(p)),
            Note(c.t(S.DELETED_WARN) + " " + c.t(S.CONTACT_LEAD), contact=True),
        ),
        c.t(S.DELETED_REASON),
    )


def identity_unlinked(c: Ctx, p: Params) -> Content:
    provider = str(p["provider"]).title()
    return Content(
        c.t(S.SUBJECT_IDENTITY_UNLINKED),
        c.t(S.UNLINKED_PRE, provider=provider),
        (
            Heading(c.t(S.UNLINKED_HEADING)),
            Paragraph(c.t(S.UNLINKED_BODY, provider=provider)),
            EventTime(c.t(S.UNLINKED_WHEN), _when(p)),
            Button(c.t(S.ACCOUNT_BUTTON), c.url("/account")),
            Note(c.t(S.UNLINKED_WARN) + " " + c.t(S.CONTACT_LEAD), contact=True),
        ),
        c.t(S.REASON_SECURITY),
    )


def totp_removed(c: Ctx, p: Params) -> Content:
    return Content(
        c.t(S.SUBJECT_TOTP_REMOVED),
        c.t(S.TOTPOFF_PRE),
        (
            Heading(c.t(S.TOTPOFF_HEADING)),
            Paragraph(c.t(S.TOTPOFF_BODY)),
            EventTime(c.t(S.TOTPOFF_WHEN), _when(p)),
            Button(c.t(S.ACCOUNT_BUTTON), c.url("/account")),
            Note(c.t(S.TOTPOFF_WARN) + " " + c.t(S.CONTACT_LEAD), contact=True),
        ),
        c.t(S.REASON_SECURITY),
    )


def connections_wiped(c: Ctx, p: Params) -> Content:
    """After ``keys wipe`` (ADR 0004 section 2): which providers were affected, that classbeat
    removed them, what to do, and the reset link if the TOTP was reset. No token, id or account
    detail (the reset link carries only its own single-use token)."""
    providers: tuple[str, ...] = tuple(p["providers"])
    reset_token: str | None = p.get("reset_token")
    blocks: list[Any] = [Heading(c.t(S.WIPED_HEADING)), Paragraph(c.t(S.WIPED_BODY))]
    if providers:
        names = ", ".join(c.t(_PROVIDER_NAMES[x]) if x in _PROVIDER_NAMES else x for x in providers)
        blocks += [
            Paragraph(c.t(S.WIPED_REMOVED, names=names)),
            Paragraph(c.t(S.WIPED_TODO)),
            Bullets(tuple(c.t(_PROVIDER_STEPS[x]) for x in providers if x in _PROVIDER_STEPS)),
        ]
    if reset_token is not None:
        blocks += [
            Paragraph(c.t(S.WIPED_RESET)),
            Button(c.t(S.WIPED_RESET_BUTTON), c.url("/reset", reset_token)),
        ]
    blocks.append(Note(c.t(S.WIPED_QUESTIONS), contact=True))
    return Content(
        c.t(S.SUBJECT_CONNECTIONS_WIPED), c.t(S.WIPED_PRE), tuple(blocks), c.t(S.REASON_SECURITY)
    )
