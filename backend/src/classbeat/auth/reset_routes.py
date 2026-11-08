"""Password reset and password change (ADR 0003 sections 5 and 6).

Reset: the request answers the same for any address and mails a 30-minute link (token in the URL
fragment, posted to the API). Completing it signs the user out everywhere, deletes their known
devices and every other outstanding token, and deletes every linked sign-in method and every
passkey the user did not keep (ADR 0019 section 9, whatever ``CLASSBEAT_PASSKEYS`` says), in the
same step: the reset is the proof, so that needs neither recent auth nor step-up. Change: recent
authentication (and step-up with a confirmed TOTP), revoking every other session.
"""

from dataclasses import asdict
from datetime import datetime
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from classbeat.auth import locale as account_locale
from classbeat.auth import mails
from classbeat.auth.accounts import find_user
from classbeat.auth.captcha import require_captcha
from classbeat.auth.clientip import bucket_ip
from classbeat.auth.deps import current_session, request_ip, require_recent_auth
from classbeat.auth.devices import forget_devices
from classbeat.auth.events import record_event
from classbeat.auth.flowtools import EmailAddress, factory, mailer, reserve
from classbeat.auth.hooks import ResetLinkSender
from classbeat.auth.passkey_recovery import passkeys_for_reset, remove_passkeys_except
from classbeat.auth.passwords import PasswordRuleError, normalize_password
from classbeat.auth.ratelimit import (
    MAIL_ACCOUNT,
    MAIL_IP,
    MAIL_PAIR,
    PASSWORD_CHANGE,
    SIGN_IN_IP,
    Bucket,
)
from classbeat.auth.sessions import SessionInfo, revoke_user_sessions
from classbeat.auth.signup_routes import Accepted, refuse
from classbeat.auth.tables import identities, users
from classbeat.auth.tokens import (
    RESET_LIFETIME,
    consume_token,
    delete_outstanding_tokens,
    issue_token,
    peek_token,
)
from classbeat.auth.userlock import lock_user
from classbeat.config import Settings
from classbeat.context import UserContext
from classbeat.db import system_session
from classbeat.emailnorm import normalize_email
from classbeat.emails.notices import happened, queue
from classbeat.mail import Mailer, send_logged

router = APIRouter(prefix="/api/auth", tags=["auth"])

FIELD_MAX = 1024
# The keep list names every method the user keeps and there is no cap on linked identities, so
# this bound is far above any real account (the page lists all of them) yet keeps the body small.
MAX_METHODS = 100
PURPOSE = "reset_password"


class ResetRequestIn(BaseModel):
    email: EmailAddress
    # The Turnstile token (ADR 0025 section 2): any value; a bad one is captcha_failed, never 422.
    captcha_token: Any = None


class TokenIn(BaseModel):
    token: str = Field(max_length=FIELD_MAX)


class LinkedMethod(BaseModel):
    id: UUID
    provider: str


class ResetPasskeyOut(BaseModel):
    id: UUID
    label: str
    created_at: datetime
    last_used_at: datetime | None


class ResetInfo(BaseModel):
    methods: list[LinkedMethod]
    passkeys: list[ResetPasskeyOut]


class ResetCompleteIn(BaseModel):
    token: str = Field(max_length=FIELD_MAX)
    password: str = Field(max_length=FIELD_MAX)
    # The linked methods the page showed (``info``) and the user kept; every other identity of
    # the user is deleted (#639). Required, so an old page that sends ``remove_methods`` gets
    # a 422 and keeps its token instead of losing every identity.
    keep_methods: list[UUID] = Field(max_length=MAX_METHODS)
    # ADR 0019 section 9: the passkeys the page showed and the user kept; every other passkey of
    # the user is deleted. Optional, unlike ``keep_methods``: a page without the passkey list
    # (before W6) sends none and the reset removes every passkey, the failure mode the ADR picks
    # (the user has just set a password and registers again), never a reset that cannot finish.
    keep_passkeys: list[UUID] = Field(default_factory=list, max_length=MAX_METHODS)


class ChangePasswordIn(BaseModel):
    new_password: str = Field(max_length=FIELD_MAX)


class Done(BaseModel):
    status: str = "ok"


async def reset_mail(
    settings: Settings, db: AsyncSession, user_id: UUID, email: str, *, mfa_reset: bool = False
):
    """Issue a reset token for the user and build the mail."""
    token = await issue_token(db, PURPOSE, user_id, RESET_LIFETIME)
    language = await account_locale.mail_locale_of(db, settings, user_id)
    build = mails.mfa_reset_link if mfa_reset else mails.password_reset
    return build(settings, email, token, language)


def make_reset_link_sender(
    settings: Settings, session_factory: async_sessionmaker[AsyncSession], mail: Mailer
) -> ResetLinkSender:
    """The sender ``auth.register_reset_link_sender`` takes: ``admin mfa-reset`` mails the
    user a reset link (awaited: the CLI has no response to send first) that says what the
    reset removed (ADR 0019 section 9)."""

    async def send(user_id: UUID, email: str) -> None:
        async with system_session(session_factory) as db:
            message = await reset_mail(settings, db, user_id, email, mfa_reset=True)
        await mail.send(message)

    return send


@router.post("/password/reset/request", status_code=202)
async def request_reset(
    body: ResetRequestIn, request: Request, background: BackgroundTasks
) -> Accepted:
    # ADR 0025 section 2: before the mail buckets, the account lookup and the mail.
    await require_captcha(request, "reset", body.captcha_token)
    normalized, ip = normalize_email(body.email), bucket_ip(request_ip(request))
    await reserve(
        request,
        [
            Bucket(MAIL_PAIR, f"{normalized}|{ip}"),
            Bucket(MAIL_ACCOUNT, normalized),
            Bucket(MAIL_IP, ip),
        ],
    )
    async with system_session(factory(request)) as db:
        user = await find_user(db, body.email)
        message = None
        if user is not None and not user.disabled:
            message = await reset_mail(request.app.state.settings, db, user.id, user.email)
    if message is not None:
        background.add_task(send_logged, mailer(request), message)
    return Accepted()


async def _invalid(request: Request) -> HTTPException:
    await reserve(request, [Bucket(SIGN_IN_IP, bucket_ip(request_ip(request)))])
    return refuse(400, "invalid_token")


@router.post("/password/reset/info")
async def reset_info(body: TokenIn, request: Request) -> ResetInfo:
    """The linked sign-in methods and passkeys the reset page offers to remove. Does not consume
    the token."""
    async with system_session(factory(request)) as db:
        token = await peek_token(db, PURPOSE, body.token)
        if token is None:
            rows, passkeys = [], []
        else:
            linked = select(identities.c.id, identities.c.provider)
            rows = (await db.execute(linked.where(identities.c.user_id == token.user_id))).all()
            passkeys = await passkeys_for_reset(db, token.user_id)
    if token is None:
        raise await _invalid(request)
    return ResetInfo(
        methods=[LinkedMethod(id=r.id, provider=r.provider) for r in rows],
        passkeys=[ResetPasskeyOut(**asdict(p)) for p in passkeys],
    )


@router.post("/password/reset/complete")
async def complete_reset(
    body: ResetCompleteIn, request: Request, background: BackgroundTasks
) -> Done:
    try:
        password = normalize_password(body.password)
    except PasswordRuleError:
        raise refuse(400, "password_rule") from None
    ip = request_ip(request)
    # ADR 0003 section 9: take the per-IP slot first (released on success), and hash only for
    # a live token, so junk requests never occupy the bounded hashing pool.
    attempt = await reserve(request, [Bucket(SIGN_IN_IP, bucket_ip(ip))])
    async with system_session(factory(request)) as db:
        live = await peek_token(db, PURPOSE, body.token)
    if live is None:
        raise refuse(400, "invalid_token")
    password_hash = await request.app.state.password_hasher.hash(password)
    keep = set(body.keep_methods)
    email: str | None = None
    async with system_session(factory(request)) as db:
        token = await consume_token(db, PURPOSE, body.token)
        if token is not None:
            # #639: the user row lock right after the token (ADR 0019 section 6). A sign-in,
            # re-auth or link that holds it commits first and is undone below; one that waits
            # finds the new hash, its session gone or (OAuth) its identity removed.
            await lock_user(db, token.user_id)
            email = (
                await db.execute(
                    update(users)
                    .where(users.c.id == token.user_id)
                    .values(password_hash=password_hash)
                    .returning(users.c.email)
                )
            ).scalar_one_or_none()  # None: the account was deleted meanwhile
            # A keep list, not a remove list: an identity linked after the page's ``info`` was
            # never shown, cannot be listed, and goes. Ids that are not the user's are ignored.
            removed = (
                await db.execute(
                    delete(identities)
                    .where(identities.c.user_id == token.user_id, identities.c.id.not_in(keep))
                    .returning(identities.c.id)
                )
            ).all()
            # The same rule for passkeys (ADR 0019 section 9), whatever the flag says.
            passkeys_removed = await remove_passkeys_except(db, token.user_id, body.keep_passkeys)
            await revoke_user_sessions(db, token.user_id)
            await forget_devices(db, token.user_id)
            await delete_outstanding_tokens(db, token.user_id)
            await record_event(
                db,
                "password_reset",
                user_id=token.user_id,
                ip=ip,
                detail={"methods_removed": len(removed), "passkeys_removed": passkeys_removed},
            )
    if token is None or email is None:
        raise refuse(400, "invalid_token")  # a parallel request consumed it
    await attempt.succeed()
    queue(background, request, happened("password_changed", email, token.user_id))
    return Done()


@router.post("/password/change")
async def change_password(
    body: ChangePasswordIn,
    request: Request,
    background: BackgroundTasks,
    ctx: Annotated[UserContext, Depends(require_recent_auth)],
    info: Annotated[SessionInfo | None, Depends(current_session)],
) -> Done:
    """Set or change the password. Every other session is revoked; the caller's stays."""
    if info is None:
        raise HTTPException(status_code=401, detail={"reason": "not_authenticated"})
    try:
        password = normalize_password(body.new_password)
    except PasswordRuleError:
        raise refuse(400, "password_rule") from None
    await reserve(request, [Bucket(PASSWORD_CHANGE, str(ctx.user_id))])
    password_hash = await request.app.state.password_hasher.hash(password)
    async with system_session(factory(request)) as db:
        email = (
            await db.execute(
                update(users)
                .where(users.c.id == ctx.user_id)
                .values(password_hash=password_hash)
                .returning(users.c.email)
            )
        ).scalar_one_or_none()  # None: the account was deleted meanwhile
        await revoke_user_sessions(db, ctx.user_id, except_id=info.id)
        await delete_outstanding_tokens(db, ctx.user_id)
        await record_event(db, "password_changed", user_id=ctx.user_id, ip=request_ip(request))
    if email is not None:
        queue(background, request, happened("password_changed", email, ctx.user_id))
    return Done()
