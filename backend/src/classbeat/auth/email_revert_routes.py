"""``POST /api/auth/email/revert``: the "this wasn't me" link of an email change (#1265).

ADR 0003 amendment of 2026-11-19. The link in the notice to the old address opens the SPA page
``/email-revert#token=...``; only its button posts the token here, so opening the link (or a mail
scanner fetching it) changes nothing, and there is no GET route. The route is public: the owner
of the old mailbox is, by assumption, no longer signed in. It never signs the browser in (section
4); the mail it sends to the restored address carries a password-reset link instead.

Every refusal of the token is one answer, ``invalid_token`` (unknown, used, expired, another
purpose, or stale because the address moved again), and each attempt holds a slot of the
sign-in IP bucket that only a success gives back, as the reset completion does. ``email_taken``
(the old address belongs to another account now) is answered only to a holder of a valid token,
which leaves the token unused.
"""

from fastapi import APIRouter, BackgroundTasks, Request
from pydantic import BaseModel, Field

from classbeat.auth import locale as account_locale
from classbeat.auth import mails
from classbeat.auth.clientip import bucket_ip
from classbeat.auth.deps import request_ip
from classbeat.auth.email_revert import Reverted, RevertRefused, revert_email_change
from classbeat.auth.flowtools import factory, mailer, reserve
from classbeat.auth.ratelimit import SIGN_IN_IP, Bucket
from classbeat.auth.signup_routes import refuse
from classbeat.db import system_session
from classbeat.mail import send_logged

router = APIRouter(prefix="/api/auth", tags=["auth"])


class RevertIn(BaseModel):
    token: str = Field(max_length=1024)


class RevertDone(BaseModel):
    status: str = "reverted"


@router.post("/email/revert")
async def revert_email(body: RevertIn, request: Request, background: BackgroundTasks) -> RevertDone:
    ip = request_ip(request)
    attempt = await reserve(request, [Bucket(SIGN_IN_IP, bucket_ip(ip))])
    settings = request.app.state.settings
    try:
        async with system_session(factory(request)) as db:
            done: Reverted = await revert_email_change(db, body.token, ip=ip)
            language = await account_locale.mail_locale_of(db, settings, done.user_id)
    except RevertRefused as exc:
        raise refuse(exc.status, exc.reason) from None  # rolled back: the token stays unused
    await attempt.succeed()
    message = mails.email_reverted_notice(settings, done.email, done.reset_token, language)
    background.add_task(send_logged, mailer(request), message)
    return RevertDone()
