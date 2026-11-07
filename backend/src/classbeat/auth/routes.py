"""``/api/auth``: the session endpoint and session management (ADR 0003 sections 3 and 4).

Sign-in itself arrives with #37 to #39. Every query here filters on the ``user_id`` of the
request's own session, and another user's session id answers 404, the same as an unknown one.

Sign-out, sign-out-everywhere and signing out one session from the list write one
``auth_events`` row (``sign_out``, ``sign_out_all``; ADR 0003 amendments of 2026-11-06, #646, and
2026-11-07, #678) in the revoke's own transaction, and only when the revoke deleted a session: a
repeated call, or a session a parallel request already ended, writes none. ``GET /api/auth/events``
lists the caller's own events (``activity``).
"""

import logging
import secrets
from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from classbeat.auth.activity import (
    CURSOR_PATTERN,
    OTHER_SESSION_KEY,
    PAGE_DEFAULT,
    PAGE_MAX,
    decode_cursor,
    list_activity,
)
from classbeat.auth.cookies import CSRF_COOKIE, clear_session_cookie, set_csrf_cookie
from classbeat.auth.deps import current_session, request_ip, require_user
from classbeat.auth.events import record_event
from classbeat.auth.middleware import csrf_token_for_session
from classbeat.auth.sessions import (
    SessionInfo,
    list_sessions,
    revoke_session,
    revoke_user_sessions,
)
from classbeat.auth.userlock import lock_user
from classbeat.context import UserContext
from classbeat.db import system_session
from classbeat.locales import offered_locales
from classbeat.log import describe_database_error

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/auth", tags=["auth"])

CSRF_VALUE_MAX = 128


class UserOut(BaseModel):
    id: UUID
    email: str
    role: str
    locale: str | None = None  # the account's language; null follows the browser (ADR 0023)


class SessionOut(BaseModel):
    id: UUID
    expires_at: datetime
    recent_auth: bool
    mfa: bool
    stepup: bool
    # The signed-in account is an admin whose TOTP check is missing or lapsed, so the page shows
    # the code prompt (ADR 0011 section 6). Always false for any other account; it reports only
    # the caller's own account, and the admin routes decide on their own.
    admin_pending_mfa: bool


class SessionState(BaseModel):
    authenticated: bool
    csrf_token: str
    user: UserOut | None = None
    session: SessionOut | None = None
    # The languages on offer (enabled and supported), for the pickers; same for everyone.
    locales: list[str] = []


class SessionItem(BaseModel):
    id: UUID
    created_at: datetime
    last_seen_at: datetime
    expires_at: datetime
    user_agent: str | None
    ip: str | None
    # Seen from a new network or browser since then (ADR 0003 amendment of 2026-11-05, #632);
    # cleared by a re-authentication, MFA or step-up in that session.
    location_changed_at: datetime | None
    current: bool


class ActivityItemOut(BaseModel):
    id: UUID
    kind: str
    at: datetime
    ip: str | None
    provider: str | None  # google, github or microsoft, for the kinds that record one
    other_session: bool  # a sign-out made from another session's list


class ActivityOut(BaseModel):
    events: list[ActivityItemOut]
    next_before: str | None  # pass as ``before`` for the next, older page; null at the end


def _factory(request: Request) -> async_sessionmaker[AsyncSession]:
    return request.app.state.session_factory


async def _record_sign_out(
    db: AsyncSession, kind: str, user_id: UUID, ip: str, detail: dict[str, object]
) -> None:
    """Write the sign-out event after the revoke, in a savepoint of the same transaction.

    Other ``auth_events`` rows are written atomically with their change (a failed insert fails
    the request). Here that would keep the session alive, so a failed insert rolls back only the
    savepoint, is logged by class (and SQLSTATE) and the revoke still commits. A failure of the
    transaction itself (a lost connection) fails the whole request: then nothing was revoked.
    """
    try:
        async with db.begin_nested():
            await record_event(db, kind, user_id=user_id, ip=ip, detail=detail)
    except DBAPIError as exc:
        logger.warning("sign-out event write failed: %s", describe_database_error(exc))
    except Exception as exc:  # the class only: a message may hold values
        logger.warning("sign-out event write failed: %s", type(exc).__name__)


@router.get("/session")
async def get_session(
    request: Request,
    response: Response,
    info: Annotated[SessionInfo | None, Depends(current_session)],
) -> SessionState:
    """Who is signed in, and the CSRF token that applies to the next unsafe request.

    With a valid session the token is ``HMAC(csrf_key, session.id)``. Without one it is the
    value of the anonymous ``__Host-classbeat_csrf`` cookie, which is set here if missing.
    """
    if info is not None:
        return SessionState(
            authenticated=True,
            csrf_token=csrf_token_for_session(request.app.state.keys, info.id),
            user=UserOut(id=info.user_id, email=info.email, role=info.role, locale=info.locale),
            session=SessionOut(
                id=info.id,
                expires_at=info.expires_at,
                recent_auth=info.recent_auth,
                mfa=info.recent_mfa,
                stepup=info.recent_stepup,
                admin_pending_mfa=info.admin_pending_mfa,
            ),
            locales=list(offered_locales(request.app.state.settings)),
        )
    value = request.cookies.get(CSRF_COOKIE, "")
    if not value or len(value) > CSRF_VALUE_MAX:
        value = secrets.token_urlsafe(32)
        set_csrf_cookie(response, value)
    return SessionState(
        authenticated=False,
        csrf_token=value,
        locales=list(offered_locales(request.app.state.settings)),
    )


@router.get("/sessions")
async def list_own_sessions(
    request: Request,
    ctx: Annotated[UserContext, Depends(require_user)],
    info: Annotated[SessionInfo | None, Depends(current_session)],
) -> list[SessionItem]:
    async with system_session(_factory(request)) as db:
        rows = await list_sessions(db, request.app.state.settings, ctx.user_id)
    return [
        SessionItem(
            id=row.id,
            created_at=row.created_at,
            last_seen_at=row.last_seen_at,
            expires_at=row.expires_at,
            user_agent=row.user_agent,
            ip=row.ip,
            location_changed_at=row.location_changed_at,
            current=info is not None and row.id == info.id,
        )
        for row in rows
    ]


@router.delete("/sessions/{session_id}", status_code=204)
async def delete_session(
    session_id: str,
    request: Request,
    response: Response,
    ctx: Annotated[UserContext, Depends(require_user)],
    info: Annotated[SessionInfo | None, Depends(current_session)],
) -> None:
    """Sign out one of the user's own sessions. 404 for an unknown id and for another user's.

    One ``sign_out`` event when it ended a session (#678): ``detail`` holds the ended session's id
    and, when another of the user's sessions ended it, that session's id (``from_session``). Ending
    this session from the list is an ordinary sign-out and carries the id only."""
    try:
        target = UUID(session_id)
    except ValueError:
        raise HTTPException(status_code=404) from None
    async with system_session(_factory(request)) as db:
        # The user row lock first, as on every revoke path (ADR 0019 section 6; see ``logout``).
        await lock_user(db, ctx.user_id)
        deleted = await revoke_session(db, ctx.user_id, target)
        if deleted:
            detail: dict[str, object] = {"session_id": str(target)}
            if info is not None and target != info.id:
                detail[OTHER_SESSION_KEY] = str(info.id)
            await _record_sign_out(db, "sign_out", ctx.user_id, request_ip(request), detail)
    if not deleted:
        raise HTTPException(status_code=404)
    if info is not None and target == info.id:
        clear_session_cookie(response)


@router.get("/events")
async def list_own_events(
    request: Request,
    ctx: Annotated[UserContext, Depends(require_user)],
    limit: Annotated[int, Query(ge=1, le=PAGE_MAX)] = PAGE_DEFAULT,
    before: Annotated[str | None, Query(pattern=CURSOR_PATTERN, max_length=60)] = None,
) -> ActivityOut:
    """The caller's own security activity, newest first (#678; ``activity``). ``before`` is the
    ``next_before`` of the previous page; a cursor that names no time we could make is a 422."""
    position = None if before is None else decode_cursor(before)
    if before is not None and position is None:
        raise HTTPException(status_code=422)
    async with system_session(_factory(request)) as db:
        page = await list_activity(db, ctx.user_id, limit=limit, before=position)
    return ActivityOut(
        events=[
            ActivityItemOut(
                id=item.id,
                kind=item.kind,
                at=item.at,
                ip=item.ip,
                provider=item.provider,
                other_session=item.other_session,
            )
            for item in page.items
        ],
        next_before=page.next_cursor,
    )


@router.post("/logout", status_code=204)
async def logout(
    request: Request,
    response: Response,
    info: Annotated[SessionInfo | None, Depends(current_session)],
) -> None:
    """Delete this session and clear the cookie. Anonymous: nothing to do.

    The user row lock comes first, as on every revoke path (ADR 0019 section 6): the event's
    foreign key takes ``FOR KEY SHARE`` on the user row after the session row is locked, which
    would deadlock with an account deletion (the user row lock, then the sessions, #675) that
    came in between. Under the lock the deletion waits for the sign-out, or the sign-out finds
    nothing."""
    if info is None:
        return
    async with system_session(_factory(request)) as db:
        await lock_user(db, info.user_id)
        if await revoke_session(db, info.user_id, info.id):
            await _record_sign_out(
                db, "sign_out", info.user_id, request_ip(request), {"session_id": str(info.id)}
            )
    clear_session_cookie(response)


@router.post("/logout-all", status_code=204)
async def logout_all(
    request: Request,
    response: Response,
    ctx: Annotated[UserContext, Depends(require_user)],
) -> None:
    """Sign out everywhere, this browser included. The user row lock comes first (ADR 0019
    section 6, #643): a sign-in or rotation committing meanwhile is waited for, so the delete
    sees its session instead of letting it survive. One ``sign_out_all`` event with the number of
    sessions ended, none when there were none left to end."""
    async with system_session(_factory(request)) as db:
        await lock_user(db, ctx.user_id)
        ended = await revoke_user_sessions(db, ctx.user_id)
        if ended:
            await _record_sign_out(
                db, "sign_out_all", ctx.user_id, request_ip(request), {"sessions": ended}
            )
    clear_session_cookie(response)
