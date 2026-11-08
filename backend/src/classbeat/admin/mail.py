"""The admin Mail page's routes (ADR 0022 sections 2 and 6; #653), on the router shell of #210.

Every route has ``admin_gate`` and does its work in one ``admin_session``. The four mail settings
(``mail_sender_name``, ``mail_support_email``, ``mail_footer``, ``mail_kinds_disabled``) are
written through ``PUT``/``DELETE /api/admin/settings/{key}`` (``classbeat.admin.settings``);
these routes read them and design and test the mails.

- ``GET /mail``: the four settings (effective value, environment value, what is stored) and every
  kind of the registry with its category, whether it is essential, whether the admin may switch it
  off and whether it is enabled now.
- ``GET /mail/{kind}/preview?part=html|text``: the mail as the production renderer builds it from
  the kind's sample data (token ``SAMPLE``) with the live branding, whatever the switch says. A
  response with its own strict policy (``default-src 'none'``, no script, ``sandbox``,
  ``frame-ancestors 'self'`` so the Mail page may frame it), ``nosniff`` and ``no-store``;
  ``classbeat.headers.SecurityHeaders`` keeps that policy for this route's success only.
- ``POST /mail/{kind}/test``: sends the same mail to the admin's own address, subject marked
  ``[Test]``, whatever the switch says; 10 per admin and hour (a refused request counts). The
  address is never in the audit row, which holds the kind id.
"""

from typing import Annotated, Literal

from fastapi import Path, Query, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, ConfigDict

from classbeat import auth
from classbeat.admin.audit import Target
from classbeat.admin.db import AdminDb, AdminRefused, admin_session
from classbeat.admin.routes import AdminActor, admin_db, admin_router
from classbeat.admin.settings import mail_flags
from classbeat.admin.settings_models import FlagOut
from classbeat.config import Settings
from classbeat.emails import live
from classbeat.emails.dispatch import build, sample_unsubscribe_links
from classbeat.emails.registry import REGISTRY, Kind
from classbeat.mail import Mailer, MailError, MailMessage

router = admin_router()

MAIL = "/mail"
KindPath = Annotated[str, Path(pattern=r"^[a-z][a-z_]{0,39}$")]
TEST_SUBJECT_PREFIX = "[Test] "


def preview_policy(settings: Settings) -> str:
    """The preview's own policy: styles inline (the layout is inline-styled), images from the
    hosted logo path only, nothing else, no script, an opaque origin (``sandbox``) and framing by
    our own pages only."""
    return (
        f"default-src 'none'; style-src 'unsafe-inline'; "
        f"img-src {settings.public_base_url}/email/; "
        "frame-ancestors 'self'; sandbox"
    )


class Out(BaseModel):
    model_config = ConfigDict(frozen=True)


class KindOut(Out):
    id: str
    category: str
    essential: bool
    user_can_opt_out: bool
    admin_can_disable: bool
    enabled: bool


class MailOut(Out):
    settings: list[FlagOut]
    kinds: list[KindOut]


class TestedOut(Out):
    sent: Literal[True] = True


def _db(request: Request) -> AdminDb:
    db = admin_db(request)
    if db is None:  # the gate answered 503 already; this keeps the type honest
        raise AdminRefused("admin_unavailable", 503)
    return db


def _kind(kind_id: str) -> Kind:
    kind = REGISTRY.get(kind_id)
    if kind is None:
        raise AdminRefused("not_found", 404)
    return kind


@router.get(MAIL, response_model=MailOut)
async def read_mail(request: Request, actor: AdminActor) -> MailOut:
    settings: Settings = request.app.state.settings
    async with admin_session(_db(request), actor, "mail.read") as session:
        config = await live.load(session, settings)
        return MailOut(
            settings=await mail_flags(session, settings),
            kinds=[
                KindOut(
                    id=k.id,
                    category=str(k.category),
                    essential=k.essential,
                    user_can_opt_out=k.user_can_opt_out,
                    admin_can_disable=k.admin_can_disable,
                    enabled=k.id not in config.disabled,
                )
                for k in REGISTRY.values()
            ],
        )


@router.get(f"{MAIL}/{{kind_id}}/preview")
async def preview_mail(
    request: Request,
    actor: AdminActor,
    kind_id: KindPath,
    part: Annotated[Literal["html", "text"], Query()] = "html",
) -> Response:
    settings: Settings = request.app.state.settings
    async with admin_session(
        _db(request),
        actor,
        "mail.preview",
        target=Target(kind="mail", id=kind_id),
        params={"kind": kind_id},
    ) as session:
        kind = _kind(kind_id)
        config = await live.load(session, settings)
    message = build(
        settings,
        kind,
        actor.email,
        kind.sample,
        branding=config.branding,
        unsubscribe=sample_unsubscribe_links(settings),
    )
    html = part == "html"
    return Response(
        message.html if html else message.body,
        media_type="text/html" if html else "text/plain",
        headers={
            "Content-Security-Policy": preview_policy(settings),
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "no-store",
        },
    )


@router.post(f"{MAIL}/{{kind_id}}/test", response_model=TestedOut)
async def test_mail(request: Request, actor: AdminActor, kind_id: KindPath) -> JSONResponse:
    settings: Settings = request.app.state.settings
    mailer: Mailer = request.app.state.mailer
    attempt = await request.app.state.limiter.begin(
        auth.admin_mail_test_buckets(str(actor.user_id))
    )
    async with admin_session(
        _db(request),
        actor,
        "mail.test",
        write=True,
        target=Target(kind="mail", id=kind_id),
        params={"kind": kind_id},
    ) as session:
        if not attempt.allowed:
            raise AdminRefused("rate_limited", 429)
        kind = _kind(kind_id)
        if not mailer.configured:
            raise AdminRefused("mail_not_configured", 409)
        config = await live.load(session, settings)
    # Sent after the audited transaction closed, so it is not held open for the SMTP timeout. A
    # failure is an `admin.request` refusal row (the handler writes it), the `mail.test` row stays.
    message = build(
        settings,
        kind,
        actor.email,
        kind.sample,
        branding=config.branding,
        unsubscribe=sample_unsubscribe_links(settings),
    )
    try:
        await mailer.send(
            MailMessage(
                to=message.to,
                subject=TEST_SUBJECT_PREFIX + message.subject,
                body=message.body,
                html=message.html,
                locale=message.locale,
                sender_name=message.sender_name,
                reply_to=message.reply_to,
            )
        )
    except MailError:
        raise AdminRefused("send_failed", 502) from None
    return JSONResponse(TestedOut().model_dump(), headers={"Cache-Control": "no-store"})
