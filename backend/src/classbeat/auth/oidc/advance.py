"""The step after authorize: issue the code, send the user to consent, or refuse (ADR 0034
section 1). Shared by the continue page and the consent POST; runs in the caller's transaction.

The request row is taken (deleted with its browser binding) only when a code or a refusal is
sent to the client: until then the user may still consent, re-authenticate or deny (#1056 N14).

The user row lock comes first (ADR 0019 section 6, #1294), then the session again: the caller
read it before this transaction, and an email revert, a reset or a sign out everywhere that
committed meanwhile ended it and burnt the user's codes (ADR 0003 "Requests in flight"). Without
the lock a code could be inserted after that burn; one that waits now finds its session gone and
is ``SessionGone``. A revert that waits for this transaction burns the code it issued.
"""

from datetime import datetime
from typing import Literal

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from classbeat.auth.events import record_event
from classbeat.auth.oidc.auth_time_policy import SessionTimes, decide
from classbeat.auth.oidc.code_store import issue_code
from classbeat.auth.oidc.flow import client_redirect
from classbeat.auth.oidc.grant_store import can_reauthenticate, consent_at, person, record_consent
from classbeat.auth.oidc.provider import Provider
from classbeat.auth.oidc.request_store import StoredRequest, take_request
from classbeat.auth.sessions import SessionInfo
from classbeat.auth.userlock import lock_user, session_alive

LOGIN_REQUIRED_HINT = "Add a password or a passkey to your classbeat account first."


class RequestGone(Exception):
    """The request was taken or expired between reading it and finishing it."""


class SessionGone(RequestGone):
    """The caller's session ended (a revert, a reset, a sign-out) before the user lock was
    taken: answered as a signed-out caller is at that route."""


def _times(info: SessionInfo) -> SessionTimes:
    return SessionTimes(info.created_at, info.reauth_at, info.stepup_at, info.totp_enabled)


async def _refuse(
    db: AsyncSession, stored: StoredRequest, binding: bytes, error: str, **extra: str
) -> str:
    if await take_request(db, stored.id, binding) is None:
        raise RequestGone
    return client_redirect(stored.redirect_uri, error=error, state=stored.state, **extra)


async def advance(
    db: AsyncSession,
    provider: Provider,
    info: SessionInfo,
    stored: StoredRequest,
    binding: bytes,
    *,
    allow: bool | None,
    passkeys: bool,
    ip: str,
) -> str | Literal["consent"]:
    """The client address to redirect to, or ``"consent"`` (continue only: show the consent
    page). ``allow`` is ``None`` at continue and the user's answer at the consent POST, where a
    missing re-authentication or step-up is the guard's 403 (the page prompts and retries)."""
    if await lock_user(db, info.user_id) is None or not await session_alive(
        db, info.user_id, info.id
    ):
        raise SessionGone
    found = await person(db, info.user_id)
    if found is None or not found.active or allow is False:
        return await _refuse(db, stored, binding, "access_denied")
    client_id = provider.client.client_id
    consented = await consent_at(db, info.user_id, client_id) is not None
    if allow is None and not consented:
        return "consent"
    able = not stored.prompt_login or await can_reauthenticate(
        db, info.user_id, role=found.role, passkeys=passkeys
    )
    decision = decide(
        _times(info),
        prompt_login=stored.prompt_login,
        received_at=stored.received_at,
        can_reauthenticate=able,
    )
    if decision.missing == "login_required":
        return await _refuse(
            db, stored, binding, "login_required", error_description=LOGIN_REQUIRED_HINT
        )
    if decision.missing is not None:
        if allow is None:
            return "consent"
        raise HTTPException(status_code=403, detail={"reason": decision.missing})
    auth_time: datetime | None = decision.auth_time
    if auth_time is None:  # decide sets it whenever nothing is missing
        raise RequestGone
    if await take_request(db, stored.id, binding) is None:
        raise RequestGone
    if allow and await record_consent(db, info.user_id, client_id):
        await record_event(db, "oidc_consent", user_id=info.user_id, ip=ip)
    code = await issue_code(db, info.user_id, provider.client, stored, auth_time)
    return client_redirect(stored.redirect_uri, code=code, state=stored.state)
