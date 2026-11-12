"""The admin's invite requests (ADR 0033 section 3), on the admin gate of ``classbeat.admin.routes``
(admin role, TOTP within 12 hours, step-up for every write, the admin rate limit, ``no-store``,
one ``admin_audit`` row per call through ``admin_session`` on ``classbeat_maint``).

- ``GET /invite-requests?state=&offset=&limit=``: newest first, with whether the address already
  has an account and what became of the issued invite.
- ``POST /invite-requests/{request_id}/approve`` ``{plan_id, grant_days, expires_days,
  send_email}``: one transaction, the request's row locked first (409 ``already_decided``): the
  invite (``auth.create_admin_invite``, the CLI's code and hashing) and, with a plan, its single-use
  redeem code (``redeem_codes.invite_id``; random bytes nobody is shown, so it is redeemed only by
  the sign-up that consumes the invite, ``auth.invite_tier``). The invite code is in this response
  only. With ``send_email`` and mail configured, the ``invite_code`` mail goes to the requester
  after the commit (``email: "queued"``); mail off sends nothing (``"mail_off"``).
- ``POST /invite-requests/{request_id}/decline``: 409 ``already_decided`` for a decided request.

The code and the address are never logged or put in an audit row; the redeem code's label is a
fixed text, so the Codes list shows these codes without naming anyone.
"""

import logging
from datetime import timedelta
from typing import Annotated, Any
from uuid import UUID, uuid4

from fastapi import BackgroundTasks, Query, Request
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from classbeat import auth
from classbeat.admin.audit import Target
from classbeat.admin.db import AdminDb, AdminRefused, admin_session
from classbeat.admin.invite_request_models import (
    ApproveIn,
    Delivery,
    InviteOut,
    Issued,
    PersonOut,
    RequestOut,
    RequestPage,
    State,
)
from classbeat.admin.routes import AdminActor, admin_db, admin_router
from classbeat.emails import live
from classbeat.mail import Mailer
from classbeat.plans.codes import new_code

logger = logging.getLogger(__name__)
router = admin_router()

PATH = "/invite-requests"
CODE_LABEL = "Invite request"

_COLUMNS = (
    "id, email, email_normalized, heard_from, instructor, locale, state, plan_id, grant_days, "
    "invite_id, decided_by, decided_at, created_at"
)
_LIST = text(
    f"SELECT {_COLUMNS} FROM invite_requests "  # noqa: S608
    "WHERE (CAST(:state AS text) IS NULL OR state = :state) "
    "ORDER BY created_at DESC, id OFFSET :offset LIMIT :limit"
)
_LOCK = text(f"SELECT {_COLUMNS} FROM invite_requests WHERE id = :i FOR UPDATE")  # noqa: S608
_DECIDE = text(
    "UPDATE invite_requests SET state = :state, plan_id = :p, grant_days = :d, "  # noqa: S608
    f"invite_id = :inv, decided_by = :by, decided_at = now() WHERE id = :i RETURNING {_COLUMNS}"
)
_PLAN_EXISTS = text("SELECT 1 FROM plans WHERE id = :p")
_INSERT_CODE = text(
    "INSERT INTO redeem_codes (id, code_hash, label, plan_id, features, grant_days, "
    "max_redemptions, expires_at, created_by, invite_id) "
    "VALUES (:i, :h, :label, :p, '{}', :d, 1, now() + make_interval(days => :e), :by, :inv)"
)


def _db(request: Request) -> AdminDb:
    db = admin_db(request)
    if db is None:  # the gate answered 503 already; this keeps the type honest
        raise AdminRefused("admin_unavailable", 503)
    return db


async def _out(session: AsyncSession, rows: list[Any]) -> list[RequestOut]:
    emails = await auth.account_emails(session, [r.decided_by for r in rows if r.decided_by])
    accounts = await auth.addresses_with_accounts(session, [r.email_normalized for r in rows])
    invites = await auth.invite_states(session, [r.invite_id for r in rows if r.invite_id])
    return [
        RequestOut(
            id=r.id,
            email=r.email,
            heard_from=r.heard_from,
            instructor=r.instructor,
            locale=r.locale,
            has_account=r.email_normalized in accounts,
            state=r.state,
            plan_id=r.plan_id,
            grant_days=r.grant_days,
            invite=InviteOut(id=r.invite_id, state=invites[r.invite_id])
            if r.invite_id in invites
            else None,
            decided_by=None
            if r.decided_by is None
            else PersonOut(id=r.decided_by, email=emails.get(r.decided_by)),
            decided_at=r.decided_at,
            created_at=r.created_at,
        )
        for r in rows
    ]


async def _pending(session: AsyncSession, request_id: UUID) -> Any:
    row = (await session.execute(_LOCK, {"i": request_id})).one_or_none()
    if row is None:
        raise AdminRefused("not_found", 404)
    if row.state != "pending":
        raise AdminRefused("already_decided", 409)
    return row


Offset = Annotated[int, Query(ge=0, le=10_000)]
Limit = Annotated[int, Query(ge=1, le=100)]


@router.get(PATH, response_model=RequestPage)
async def list_requests(
    request: Request,
    actor: AdminActor,
    state: State | None = None,
    offset: Offset = 0,
    limit: Limit = 50,
) -> RequestPage:
    params = {"state": state, "offset": offset, "limit": limit}
    async with admin_session(_db(request), actor, "invite_request.list", params=params) as session:
        rows = (
            await session.execute(_LIST, {"state": state, "offset": offset, "limit": limit + 1})
        ).all()
        page = await _out(session, list(rows[:limit]))
    return RequestPage(requests=page, next_offset=offset + limit if len(rows) > limit else None)


@router.post(f"{PATH}/{{request_id}}/approve", status_code=201, response_model=Issued)
async def approve_request(
    request: Request,
    actor: AdminActor,
    request_id: UUID,
    body: ApproveIn,
    background: BackgroundTasks,
) -> Issued:
    params = {
        "plan_id": body.plan_id,
        "grant_days": body.grant_days,
        "expires_days": body.expires_days,
        "send_email": body.send_email,
    }
    async with admin_session(
        _db(request),
        actor,
        "invite_request.approve",
        write=True,
        target=Target(kind="invite_request", id=str(request_id)),
        params=params,
    ) as session:
        pending = await _pending(session, request_id)
        if (
            body.plan_id is not None
            and (await session.execute(_PLAN_EXISTS, {"p": body.plan_id})).first() is None
        ):
            raise AdminRefused("unknown_plan", 422)
        invite_id = uuid4()
        code, _ = await auth.create_admin_invite(
            session,
            timedelta(days=body.expires_days),
            invite_id=invite_id,
            created_by=actor.user_id,
        )
        if body.plan_id is not None:
            _, digest = new_code()  # the code's text is dropped: only the sign-up redeems it
            await session.execute(
                _INSERT_CODE,
                {
                    "i": uuid4(),
                    "h": digest,
                    "label": CODE_LABEL,
                    "p": body.plan_id,
                    "d": body.grant_days,
                    "e": body.expires_days,
                    "by": actor.user_id,
                    "inv": invite_id,
                },
            )
        decided = {"state": "issued", "p": body.plan_id, "d": body.grant_days, "inv": invite_id}
        row = (
            await session.execute(_DECIDE, {**decided, "by": actor.user_id, "i": request_id})
        ).one()
        (out,) = await _out(session, [row])
    email: Delivery = "not_requested"
    if body.send_email:
        mailer: Mailer = request.app.state.mailer
        email = "queued" if mailer.configured else "mail_off"
        if mailer.configured:
            background.add_task(
                _mail_code, request.app, pending.email, code, body.expires_days, pending.locale
            )
    return Issued(code=code, request=out, email=email)


@router.post(f"{PATH}/{{request_id}}/decline", response_model=RequestOut)
async def decline_request(request: Request, actor: AdminActor, request_id: UUID) -> RequestOut:
    async with admin_session(
        _db(request),
        actor,
        "invite_request.decline",
        write=True,
        target=Target(kind="invite_request", id=str(request_id)),
    ) as session:
        await _pending(session, request_id)
        values = {"state": "declined", "p": None, "d": None, "inv": None}
        row = (
            await session.execute(_DECIDE, {**values, "by": actor.user_id, "i": request_id})
        ).one()
        (out,) = await _out(session, [row])
    return out


async def _mail_code(app: Any, recipient: str, code: str, days: int, locale: str | None) -> None:
    """The background send of the ``invite_code`` mail through the one send path (essential: no
    preference or switch applies). Never raises; logs the error class only."""
    try:
        state = app.state
        await live.send_optional(
            state.session_factory,
            state.mailer,
            state.settings,
            "invite_code",
            recipient,
            {"code": code, "days": days},
            locale=locale,
        )
    except Exception as exc:
        logger.warning("mail not sent (invite_code): %s", type(exc).__name__)
