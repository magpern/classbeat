"""The admin area's Users (ADR 0011 section 6, AD1 #218), on the router shell of #210.

Every route has ``admin_gate`` (admin role, TOTP within 12 hours, step-up within 5 minutes for
every write, the admin rate limit, ``no-store``; CSRF by the auth middleware) and runs its
database work in one ``admin_session``: the audit row first, then reads read only. A target that
does not exist is 404 ``not_found``; a refused action is an ``AdminRefused`` (one ``refused``
audit row, nothing changed).

- ``GET /users?q=&offset=&limit=``: email, created, role, disabled, the effective plan with its
  source and end, classes held this period of the plan's limit, this period's model cost.
- ``GET /users/{user_id}``: the same plus every entitlement (with its note), the credit ledger
  of the last three periods and the connection status of each provider. Never a token,
  ciphertext, hash, Client ID, playlist id, brief, job input or class content: ``classbeat_maint``
  cannot read them, and nothing here asks.
- ``POST /users/{user_id}/entitlements``: grant a plan and/or features from now, with an optional
  end and note. ``POST .../entitlements/{entitlement_id}/revoke``: revoke one (any source; rows
  are never deleted).
- ``POST /users/{user_id}/disable`` (revokes all the user's sessions) and ``.../enable``.
- ``DELETE /users/{user_id}`` with ``{"email": ...}`` typed to confirm: ADR 0003's account
  deletion of the target. The audit row keeps the id in ``target_id`` (the foreign key nulls
  ``target_user_id`` in the same transaction).

Disable and delete refuse a target whose role is ``admin``, the acting admin included (409
``target_is_admin``). No route changes ``users.role``.

The effective plan, its source and the class limit are ``classbeat.admin.users_summary``'s: the
resolver's rule (ADR 0031 section 2: a stored admin is on the top plan, source ``admin``) as one
set query over the page. The account tables' statements live in ``classbeat.auth`` and the
connection status in ``classbeat.connections`` (guards 8 and 4).
"""

from typing import Annotated, Any
from uuid import UUID

from fastapi import Query, Request, Response
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from classbeat import auth
from classbeat.admin.audit import Target
from classbeat.admin.audit_text import UserQuery
from classbeat.admin.db import AdminDb, AdminRefused, admin_session
from classbeat.admin.routes import AdminActor, admin_db, admin_router
from classbeat.admin.users_models import (
    ConnectionOut,
    DeleteIn,
    DisabledOut,
    EntitlementOut,
    GrantIn,
    LedgerOut,
    UserDetail,
    UserPage,
)
from classbeat.admin.users_summary import MONTH, summaries
from classbeat.connections import connection_statuses
from classbeat.emailnorm import normalize_email
from classbeat.quota.bounds import Bounds

router = admin_router()

LEDGER_ROWS = 1000
ENTITLEMENT_ROWS = 200

_ENTITLEMENT_COLUMNS = (
    "id, plan_id, features, source, starts_at, ends_at, granted_by, note, revoked_at, "
    "revoked_by, created_at, (revoked_at IS NULL AND starts_at <= now() "
    "AND (ends_at IS NULL OR ends_at > now())) AS active"
)
_ENTITLEMENTS = text(
    f"SELECT {_ENTITLEMENT_COLUMNS} FROM entitlements WHERE owner_id = :o "  # noqa: S608
    "ORDER BY created_at DESC, id LIMIT :n"
)
_LEDGER = text(
    "SELECT id, job_id, period, plan_id, state, reason, settled_at, refunded_by, created_at "  # noqa: S608
    "FROM credit_ledger WHERE owner_id = :o "
    f"AND period >= CAST({MONTH} - interval '2 months' AS date) "
    "ORDER BY created_at DESC, id LIMIT :n"
)
_PLAN_EXISTS = text("SELECT 1 FROM plans WHERE id = :p")
_IN_FUTURE = text("SELECT CAST(:e AS timestamptz) > now()")
_WITHIN_TESTER_PERIOD = text("SELECT CAST(:e AS timestamptz) <= now() + interval '90 days'")
_GRANT = text(
    "INSERT INTO entitlements (owner_id, plan_id, features, source, ends_at, granted_by, note) "  # noqa: S608
    "VALUES (:o, :p, :f, 'grant', :e, :by, :note) "
    f"RETURNING {_ENTITLEMENT_COLUMNS}"
)
_REVOKE = text(
    "UPDATE entitlements SET revoked_at = now(), revoked_by = :by "  # noqa: S608
    "WHERE id = :e AND owner_id = :o AND revoked_at IS NULL "
    f"RETURNING {_ENTITLEMENT_COLUMNS}"
)
_REVOKED_AT = text("SELECT revoked_at FROM entitlements WHERE id = :e AND owner_id = :o")


def _db(request: Request) -> AdminDb:
    db = admin_db(request)
    if db is None:  # the gate answered 503 already; this keeps the type honest
        raise AdminRefused("admin_unavailable", 503)
    return db


def _bounds(request: Request) -> Bounds:
    return Bounds.from_settings(request.app.state.settings)


def _user(user_id: UUID) -> Target:
    """The target of a user action: ``target_user_id`` (nulled when the account goes, ADR 0011
    section 1); an id that is no user is answered 404 by ``admin_session``."""
    return Target(user_id=user_id)


def _entitlement(row: Any) -> EntitlementOut:
    return EntitlementOut(
        id=row.id,
        plan_id=row.plan_id,
        features=list(row.features),
        source=row.source,
        starts_at=row.starts_at,
        ends_at=row.ends_at,
        granted_by=row.granted_by,
        note=row.note,
        revoked_at=row.revoked_at,
        revoked_by=row.revoked_by,
        created_at=row.created_at,
        active=bool(row.active),
    )


async def _target(session: AsyncSession, user_id: UUID, *, lock: bool = False) -> auth.AccountRow:
    found = await auth.get_account(session, user_id, lock=lock)
    if found is None:
        raise AdminRefused("not_found", 404)
    return found


@router.get("/users", response_model=UserPage)
async def list_users(
    request: Request,
    actor: AdminActor,
    q: Annotated[UserQuery | None, Query()] = None,
    offset: Annotated[int, Query(ge=0, le=10_000)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> UserPage:
    prefix = q.strip() if q and q.strip() else None
    # The text is a prefix of someone's email: only its length is audited (ADR 0014 section 5).
    params = {"q_length": len(prefix) if prefix else None, "offset": offset, "limit": limit}
    async with admin_session(_db(request), actor, "user.list", params=params) as session:
        accounts = await auth.find_accounts(session, prefix=prefix, offset=offset, limit=limit + 1)
        page = await summaries(session, accounts[:limit], _bounds(request))
    return UserPage(users=page, next_offset=offset + limit if len(accounts) > limit else None)


@router.get("/users/{user_id}", response_model=UserDetail)
async def read_user(request: Request, actor: AdminActor, user_id: UUID) -> UserDetail:
    async with admin_session(_db(request), actor, "user.read", target=_user(user_id)) as session:
        found = await _target(session, user_id)
        (summary,) = await summaries(session, [found], _bounds(request))
        entitlements = await session.execute(_ENTITLEMENTS, {"o": user_id, "n": ENTITLEMENT_ROWS})
        ledger = await session.execute(_LEDGER, {"o": user_id, "n": LEDGER_ROWS})
        statuses = await connection_statuses(session, user_id)
        return UserDetail(
            **summary.model_dump(),
            entitlements=[_entitlement(row) for row in entitlements.all()],
            ledger=[
                LedgerOut(
                    id=row.id,
                    job_id=row.job_id,
                    period=row.period.isoformat(),
                    plan_id=row.plan_id,
                    state=row.state,
                    reason=row.reason,
                    settled_at=row.settled_at,
                    refunded_by=row.refunded_by,
                    created_at=row.created_at,
                )
                for row in ledger.all()
            ],
            connections=[
                ConnectionOut(
                    provider=s.provider,
                    status=s.status,
                    last_error=s.last_error,
                    connected_at=s.connected_at,
                    last_used_at=s.last_used_at,
                )
                for s in statuses
            ],
        )


@router.post("/users/{user_id}/entitlements", status_code=201, response_model=EntitlementOut)
async def grant_entitlement(
    request: Request, actor: AdminActor, user_id: UUID, body: GrantIn
) -> EntitlementOut:
    params = {
        "plan_id": body.plan_id,
        "features": list(body.features),
        "ends_at": None if body.ends_at is None else body.ends_at.isoformat(),
        "note": body.note is not None,
    }
    async with admin_session(
        _db(request), actor, "user.grant", write=True, target=_user(user_id), params=params
    ) as session:
        await _target(session, user_id)
        if (
            body.plan_id is not None
            and (await session.execute(_PLAN_EXISTS, {"p": body.plan_id})).first() is None
        ):
            raise AdminRefused("unknown_plan", 422)
        if (
            body.ends_at is not None
            and not (await session.execute(_IN_FUTURE, {"e": body.ends_at})).scalar_one()
        ):
            raise AdminRefused("ends_in_past", 422)
        if "billing_test" in body.features and (
            body.ends_at is None
            or not (await session.execute(_WITHIN_TESTER_PERIOD, {"e": body.ends_at})).scalar_one()
        ):
            # ADR 0031 section 6: a tester grant has an end, at most 90 days out.
            raise AdminRefused("billing_test_period", 422)
        row = (
            await session.execute(
                _GRANT,
                {
                    "o": user_id,
                    "p": body.plan_id,
                    "f": list(body.features),
                    "e": body.ends_at,
                    "by": actor.user_id,
                    "note": body.note,
                },
            )
        ).one()
        return _entitlement(row)


@router.post("/users/{user_id}/entitlements/{entitlement_id}/revoke", response_model=EntitlementOut)
async def revoke_entitlement(
    request: Request, actor: AdminActor, user_id: UUID, entitlement_id: UUID
) -> EntitlementOut:
    target = Target(user_id=user_id, kind="entitlement", id=str(entitlement_id))
    async with admin_session(
        _db(request), actor, "user.revoke", write=True, target=target
    ) as session:
        params = {"e": entitlement_id, "o": user_id}
        row = (await session.execute(_REVOKE, {**params, "by": actor.user_id})).one_or_none()
        if row is None:
            if (await session.execute(_REVOKED_AT, params)).first() is None:
                raise AdminRefused("not_found", 404)
            raise AdminRefused("already_revoked", 409)
        return _entitlement(row)


@router.post("/users/{user_id}/disable", response_model=DisabledOut)
async def disable_user(request: Request, actor: AdminActor, user_id: UUID) -> DisabledOut:
    async with admin_session(
        _db(request), actor, "user.disable", write=True, target=_user(user_id)
    ) as session:
        found = await _target(session, user_id, lock=True)
        if found.role == "admin":
            raise AdminRefused("target_is_admin", 409)
        disabled_at, revoked = await auth.disable_account(session, user_id)
        return DisabledOut(id=user_id, disabled_at=disabled_at, sessions_revoked=revoked)


@router.post("/users/{user_id}/enable", response_model=DisabledOut)
async def enable_user(request: Request, actor: AdminActor, user_id: UUID) -> DisabledOut:
    async with admin_session(
        _db(request), actor, "user.enable", write=True, target=_user(user_id)
    ) as session:
        await _target(session, user_id, lock=True)
        await auth.enable_account(session, user_id)
        return DisabledOut(id=user_id, disabled_at=None, sessions_revoked=0)


@router.delete("/users/{user_id}", status_code=204)
async def delete_user(
    request: Request, actor: AdminActor, user_id: UUID, body: DeleteIn
) -> Response:
    async with admin_session(
        _db(request),
        actor,
        "user.delete",
        write=True,
        target=Target(user_id=user_id, kind="user", id=str(user_id)),  # survives the delete
    ) as session:
        # Checked without the row lock: ending the target's jobs commits in the target's own
        # sessions and can wait for a running job (ADR 0014 section 3), and the user row lock
        # is not held across that wait. The lock (``FOR NO KEY UPDATE``, #675) is taken after.
        found = await _target(session, user_id)
        if found.role == "admin":
            raise AdminRefused("target_is_admin", 409)
        if normalize_email(body.email) != found.email_normalized:
            raise AdminRefused("email_mismatch", 409)
        # Through a value installed by ``web``: the jobs code is not reached from the admin
        # package (guard 11), and it runs in the target's own sessions, not this one.
        await request.app.state.end_account_jobs(user_id)
        found = await _target(session, user_id, lock=True)  # the role may have changed meanwhile
        if found.role == "admin":
            raise AdminRefused("target_is_admin", 409)
        await auth.delete_account(session, user_id)
    return Response(status_code=204)
