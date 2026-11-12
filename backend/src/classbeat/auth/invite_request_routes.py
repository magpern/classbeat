"""Request an invite (ADR 0033 section 2): ``POST /api/auth/invite-request``.

Anonymous, so it is built like sign-up (ADR 0025 section 2): the body's shape and the checks of
the body alone, then Turnstile (action ``invite_request``), then the buckets (per normalized
address and per IP, every request counts), then the sign-up mode: only ``invite`` takes requests
(403 ``requests_closed`` otherwise; the mode is public anyway, ``GET /api/settings/public``).

The answer is always 202 ``{"status": "received"}``: a new request, a second one while the first
is pending (``invite_requests_one_open``), one past ``PENDING_CAP``, and an address that already
has an account all look the same. Nothing here reads ``users``: whether an address has an account
is the admin's to see, never the visitor's. No log line names the address or the text, and no mail
is sent (the code goes out only when an admin issues one).

The hourly purge of these rows is ``classbeat.auth.invite_request_purge`` (the worker imports no
web route).
"""

import logging
from typing import Annotated, Any

from fastapi import APIRouter, Request
from pydantic import AfterValidator, BaseModel, Field, StrictBool
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from classbeat.auth import locale as account_locale
from classbeat.auth.captcha import require_captcha
from classbeat.auth.clientip import bucket_ip
from classbeat.auth.deps import request_ip
from classbeat.auth.flowtools import EmailAddress, factory, reserve
from classbeat.auth.invite_request_policy import HEARD_FROM_MAX, PENDING_CAP, clean_heard_from
from classbeat.auth.ratelimit import Bucket, Limit
from classbeat.auth.signup_routes import refuse
from classbeat.db import system_session
from classbeat.emailnorm import normalize_email
from classbeat.flags import effective as flags

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/auth", tags=["auth"])

# ADR 0003 section 9's mechanism (fixed windows of at most an hour; every request counts).
REQUEST_ADDRESS = Limit("invite-request", "account", 3, 3600)
REQUEST_IP = Limit("invite-request", "ip", 5, 3600)

# Stored only while fewer than ``PENDING_CAP`` requests are pending (a concurrent burst can pass
# the count by a few; the cap bounds the table, it is not exact).
_INSERT = text(
    "INSERT INTO invite_requests (email, email_normalized, heard_from, instructor, locale) "
    "SELECT :email, :normalized, :heard_from, :instructor, :locale "
    "WHERE (SELECT count(*) FROM invite_requests WHERE state = 'pending') < :cap"
)
_ONE_OPEN = "invite_requests_one_open"


class InviteRequestIn(BaseModel):
    email: EmailAddress
    heard_from: Annotated[
        str, Field(max_length=4 * HEARD_FROM_MAX), AfterValidator(clean_heard_from)
    ] = ""
    instructor: StrictBool
    # The app's language (ADR 0023 section 2): any value; one that is not offered is ignored.
    locale: Any = None
    # The Turnstile token (ADR 0025 section 2): any value; a bad one is captcha_failed, never 422.
    captcha_token: Any = None


class Received(BaseModel):
    status: str = "received"


async def _store(db: AsyncSession, values: dict[str, Any]) -> bool:
    """Insert the request; ``False`` when the address already has a pending one or the queue is
    full (both answered as a success)."""
    try:
        async with db.begin_nested():
            result = await db.execute(_INSERT, {**values, "cap": PENDING_CAP})
    except IntegrityError as error:
        diag = getattr(error.orig, "diag", None)
        if getattr(diag, "constraint_name", None) == _ONE_OPEN:
            return False
        raise RuntimeError("invite request not stored") from None  # DETAIL holds the address
    if not result.rowcount:  # pyright: ignore[reportAttributeAccessIssue]
        logger.warning("invite request not stored: %d requests are pending", PENDING_CAP)
        return False
    return True


@router.post("/invite-request", status_code=202)
async def request_invite(body: InviteRequestIn, request: Request) -> Received:
    normalized = normalize_email(body.email)
    await require_captcha(request, "invite_request", body.captcha_token)
    await reserve(
        request,
        [Bucket(REQUEST_ADDRESS, normalized), Bucket(REQUEST_IP, bucket_ip(request_ip(request)))],
    )
    settings = request.app.state.settings
    async with system_session(factory(request)) as db:
        if await flags.signup_mode(db, settings) != "invite":
            raise refuse(403, "requests_closed")
        await _store(
            db,
            {
                "email": body.email,
                "normalized": normalized,
                "heard_from": body.heard_from,
                "instructor": body.instructor,
                "locale": account_locale.for_request(request, body.locale),
            },
        )
    return Received()
