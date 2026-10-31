"""The admin area's Codes (ADR 0011 sections 1, 2 and 6; AD2 #220), on the router shell of #210.

Every route has ``admin_gate`` (admin role, TOTP within 12 hours, step-up within 5 minutes for
every write, the admin rate limit, ``no-store``; CSRF by the auth middleware) and runs its
database work in one ``admin_session`` on ``classbeat_maint``: the audit row first, then reads
read only. An id that is no invite or code is 404 ``not_found``; a refused action is an
``AdminRefused`` (one ``refused`` audit row, nothing changed).

- ``GET /invites?offset=&limit=``: invites, newest first: who created it (``null`` for the CLI),
  who used it, when it was created, expires and was used, and its state.
- ``POST /invites`` ``{expires_days}``: the CLI's invite (``classbeat.auth``: 16 random bytes,
  only the SHA-256 stored) with ``created_by`` = the admin.
- ``POST /invites/{invite_id}/disable``: expires an unused invite now, so the unchanged M1
  consume path refuses it (409 ``invite_used`` or ``already_expired`` otherwise).
- ``GET /redeem-codes?offset=&limit=``: label, plan, features, days, uses, expiry, disabled,
  creator and state.
- ``POST /redeem-codes`` ``{label, plan_id, features, grant_days, max_redemptions,
  expires_at}``: a code from ``classbeat.plans.codes`` (16 random bytes, Crockford base32, only
  the SHA-256 stored).
- ``POST /redeem-codes/{code_id}/disable``: sets ``disabled_at`` on an active code, so PL1's
  redeem answers ``code_invalid``; 409 ``already_disabled``, ``already_expired`` or ``used_up``
  otherwise, read from the code's locked row.

**The code is in the create response only.** No list, audit row (``params`` never hold a code,
hash or label) or log line carries it, and ``classbeat_maint`` cannot read either hash column.
Both creates pick the row id first, so the audit row (written before the insert) names it.

Maint writes on ``redeem_codes`` are ``INSERT`` and ``UPDATE (disabled_at)`` only; nothing here
changes ``redeemed_count`` (``redeem_codes_guard``, #271, leaves maint's disable alone).
"""

from datetime import datetime, timedelta
from typing import Annotated, Any
from uuid import UUID, uuid4

from fastapi import Query, Request
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, StringConstraints, model_validator
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from classbeat import auth
from classbeat.admin.audit import LabelText, Target
from classbeat.admin.db import AdminDb, AdminRefused, admin_session
from classbeat.admin.routes import AdminActor, admin_db, admin_router
from classbeat.plans.codes import new_code
from classbeat.plans.tables import FEATURES

router = admin_router()

INVITES = "/invites"
CODES = "/redeem-codes"

_CODE_COLUMNS = (
    "id, label, plan_id, features, grant_days, max_redemptions, redeemed_count, expires_at, "
    "disabled_at, created_by, created_at, CASE WHEN disabled_at IS NOT NULL THEN 'disabled' "
    "WHEN expires_at <= now() THEN 'expired' WHEN redeemed_count >= max_redemptions "
    "THEN 'used_up' ELSE 'active' END AS state"
)
_LIST_CODES = text(
    f"SELECT {_CODE_COLUMNS} FROM redeem_codes "  # noqa: S608
    "ORDER BY created_at DESC, id OFFSET :offset LIMIT :limit"
)
_PLAN_EXISTS = text("SELECT 1 FROM plans WHERE id = :p")
_IN_FUTURE = text("SELECT CAST(:e AS timestamptz) > now()")
_INSERT_CODE = text(
    "INSERT INTO redeem_codes (id, code_hash, label, plan_id, features, grant_days, "  # noqa: S608
    "max_redemptions, expires_at, created_by) VALUES (:i, :h, :label, :p, :f, :d, :m, :e, :by) "
    f"RETURNING {_CODE_COLUMNS}"
)
# The code's row locked first, so its state (and the 409's reason) is the one the disable sees,
# not a second read after a parallel redeem or disable (#227).
_LOCK_CODE = text(f"SELECT {_CODE_COLUMNS} FROM redeem_codes WHERE id = :i FOR UPDATE")  # noqa: S608
_DISABLE_CODE = text(
    "UPDATE redeem_codes SET disabled_at = now() WHERE id = :i AND disabled_at IS NULL "  # noqa: S608
    f"RETURNING {_CODE_COLUMNS}"
)
# A code no redeem can use any more answers as invites do (#279 review): nothing to disable.
_NOT_ACTIVE = {"disabled": "already_disabled", "expired": "already_expired", "used_up": "used_up"}

# A label is shown as text in the admin pages: no control character (C0 and C1), no format
# character (bidi, zero-width; #227), at most 80 characters.
Label = LabelText


class Out(BaseModel):
    model_config = ConfigDict(frozen=True)


class PersonOut(Out):
    id: UUID
    email: str | None  # None: the account is gone


class InviteOut(Out):
    id: UUID
    created_by: PersonOut | None  # None: created with the CLI
    used_by: PersonOut | None
    created_at: datetime
    expires_at: datetime
    used_at: datetime | None
    state: str  # active, used or expired


class InvitePage(Out):
    invites: list[InviteOut]
    next_offset: int | None


class InviteCreated(Out):
    code: str  # shown once; only its hash is stored
    invite: InviteOut


class RedeemCodeOut(Out):
    id: UUID
    label: str
    plan_id: str | None
    features: list[str]
    grant_days: int
    max_redemptions: int
    redeemed_count: int
    expires_at: datetime
    disabled_at: datetime | None
    created_by: PersonOut | None
    created_at: datetime
    state: str  # active, used_up, expired or disabled


class RedeemCodePage(Out):
    redeem_codes: list[RedeemCodeOut]
    next_offset: int | None


class RedeemCodeCreated(Out):
    code: str  # shown once; only its hash is stored
    redeem_code: RedeemCodeOut


class In(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class InviteIn(In):
    expires_days: Annotated[int, Field(ge=1, le=365)] = 7  # the CLI's default and bound


class RedeemCodeIn(In):
    label: Label
    plan_id: Annotated[str, StringConstraints(pattern=r"^[a-z_]{1,30}$")] | None = None
    features: Annotated[list[str], Field(max_length=len(FEATURES))] = []
    grant_days: Annotated[int, Field(ge=1, le=366)]
    max_redemptions: Annotated[int, Field(ge=1, le=1000)]
    expires_at: AwareDatetime

    @model_validator(mode="after")
    def _grants_something(self) -> "RedeemCodeIn":
        if self.plan_id is None and not self.features:
            raise ValueError("a code names a plan or a feature")
        if not set(self.features) <= FEATURES or len(set(self.features)) != len(self.features):
            raise ValueError("features are distinct codes of the feature list")
        return self


def _db(request: Request) -> AdminDb:
    db = admin_db(request)
    if db is None:  # the gate answered 503 already; this keeps the type honest
        raise AdminRefused("admin_unavailable", 503)
    return db


def _person(user_id: UUID | None, emails: dict[UUID, str]) -> PersonOut | None:
    return None if user_id is None else PersonOut(id=user_id, email=emails.get(user_id))


def _invite(row: auth.InviteRow, emails: dict[UUID, str]) -> InviteOut:
    return InviteOut(
        id=row.id,
        created_by=_person(row.created_by, emails),
        used_by=_person(row.used_by, emails),
        created_at=row.created_at,
        expires_at=row.expires_at,
        used_at=row.used_at,
        state=row.state,
    )


async def _invites_out(session: AsyncSession, rows: list[auth.InviteRow]) -> list[InviteOut]:
    people = [i for row in rows for i in (row.created_by, row.used_by) if i is not None]
    emails = await auth.account_emails(session, people)
    return [_invite(row, emails) for row in rows]


async def _codes_out(session: AsyncSession, rows: list[Any]) -> list[RedeemCodeOut]:
    emails = await auth.account_emails(session, [r.created_by for r in rows if r.created_by])
    return [
        RedeemCodeOut(
            id=row.id,
            label=row.label,
            plan_id=row.plan_id,
            features=list(row.features),
            grant_days=row.grant_days,
            max_redemptions=row.max_redemptions,
            redeemed_count=row.redeemed_count,
            expires_at=row.expires_at,
            disabled_at=row.disabled_at,
            created_by=_person(row.created_by, emails),
            created_at=row.created_at,
            state=row.state,
        )
        for row in rows
    ]


Offset = Annotated[int, Query(ge=0, le=10_000)]
Limit = Annotated[int, Query(ge=1, le=100)]


# --- invites ----------------------------------------------------------------------------------


@router.get(INVITES, response_model=InvitePage)
async def list_invites(
    request: Request, actor: AdminActor, offset: Offset = 0, limit: Limit = 50
) -> InvitePage:
    params = {"offset": offset, "limit": limit}
    async with admin_session(_db(request), actor, "invite.list", params=params) as session:
        rows = await auth.list_invites(session, offset=offset, limit=limit + 1)
        page = await _invites_out(session, rows[:limit])
    return InvitePage(invites=page, next_offset=offset + limit if len(rows) > limit else None)


@router.post(INVITES, status_code=201, response_model=InviteCreated)
async def create_invite(request: Request, actor: AdminActor, body: InviteIn) -> InviteCreated:
    invite_id = uuid4()
    async with admin_session(
        _db(request),
        actor,
        "invite.create",
        write=True,
        target=Target(kind="invite", id=str(invite_id)),
        params={"expires_days": body.expires_days},
    ) as session:
        code, row = await auth.create_admin_invite(
            session,
            timedelta(days=body.expires_days),
            invite_id=invite_id,
            created_by=actor.user_id,
        )
        (invite,) = await _invites_out(session, [row])
    return InviteCreated(code=code, invite=invite)


@router.post(f"{INVITES}/{{invite_id}}/disable", response_model=InviteOut)
async def disable_invite(request: Request, actor: AdminActor, invite_id: UUID) -> InviteOut:
    async with admin_session(
        _db(request),
        actor,
        "invite.disable",
        write=True,
        target=Target(kind="invite", id=str(invite_id)),
    ) as session:
        changed, row = await auth.disable_invite(session, invite_id)
        if row is None:
            raise AdminRefused("not_found", 404)
        if not changed:
            raise AdminRefused("invite_used" if row.state == "used" else "already_expired", 409)
        (invite,) = await _invites_out(session, [row])
    return invite


# --- redeem codes -----------------------------------------------------------------------------


@router.get(CODES, response_model=RedeemCodePage)
async def list_redeem_codes(
    request: Request, actor: AdminActor, offset: Offset = 0, limit: Limit = 50
) -> RedeemCodePage:
    params = {"offset": offset, "limit": limit}
    async with admin_session(_db(request), actor, "code.list", params=params) as session:
        rows = (await session.execute(_LIST_CODES, {"offset": offset, "limit": limit + 1})).all()
        page = await _codes_out(session, list(rows[:limit]))
    return RedeemCodePage(
        redeem_codes=page, next_offset=offset + limit if len(rows) > limit else None
    )


@router.post(CODES, status_code=201, response_model=RedeemCodeCreated)
async def create_redeem_code(
    request: Request, actor: AdminActor, body: RedeemCodeIn
) -> RedeemCodeCreated:
    code_id = uuid4()
    params = {
        "plan_id": body.plan_id,
        "features": list(body.features),
        "grant_days": body.grant_days,
        "max_redemptions": body.max_redemptions,
        "expires_at": body.expires_at.isoformat(),
    }
    async with admin_session(
        _db(request),
        actor,
        "code.create",
        write=True,
        target=Target(kind="redeem_code", id=str(code_id)),
        params=params,
    ) as session:
        if (
            body.plan_id is not None
            and (await session.execute(_PLAN_EXISTS, {"p": body.plan_id})).first() is None
        ):
            raise AdminRefused("unknown_plan", 422)
        if not (await session.execute(_IN_FUTURE, {"e": body.expires_at})).scalar_one():
            raise AdminRefused("expires_in_past", 422)
        code, digest = new_code()
        row = (
            await session.execute(
                _INSERT_CODE,
                {
                    "i": code_id,
                    "h": digest,
                    "label": body.label,
                    "p": body.plan_id,
                    "f": list(body.features),
                    "d": body.grant_days,
                    "m": body.max_redemptions,
                    "e": body.expires_at,
                    "by": actor.user_id,
                },
            )
        ).one()
        (out,) = await _codes_out(session, [row])
    return RedeemCodeCreated(code=code, redeem_code=out)


@router.post(f"{CODES}/{{code_id}}/disable", response_model=RedeemCodeOut)
async def disable_redeem_code(request: Request, actor: AdminActor, code_id: UUID) -> RedeemCodeOut:
    async with admin_session(
        _db(request),
        actor,
        "code.disable",
        write=True,
        target=Target(kind="redeem_code", id=str(code_id)),
    ) as session:
        locked = (await session.execute(_LOCK_CODE, {"i": code_id})).one_or_none()
        if locked is None:
            raise AdminRefused("not_found", 404)
        if locked.state in _NOT_ACTIVE:
            raise AdminRefused(_NOT_ACTIVE[locked.state], 409)
        row = (await session.execute(_DISABLE_CODE, {"i": code_id})).one()
        (out,) = await _codes_out(session, [row])
    return out
