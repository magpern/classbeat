"""Re-authenticate with a passkey: ``POST /api/auth/passkeys/reauth/start`` and ``.../finish``
(ADR 0019 sections 6, 7, 8, 10 and 13, #774).

Both need ``CLASSBEAT_PASSKEYS`` (else 404 ``feature_off``, answered first) and a signed-in
session (``require_user``; recent authentication is what they give, so never required), plus
CSRF and Origin (the middleware).

**Start** locks the user row first (section 6, as the register start: the previous flow's delete
locks a challenge row that a reset's session delete cascades to), checks the session is still
there, stores a 32-byte challenge bound to this user and **this session** and sets the ``reauth``
flow cookie (the browser's previous re-auth flow goes first; a register or sign-in flow is kept).
``allowCredentials`` names the user's own credentials, a hint to the browser only.

**Finish**, in order:

1. The password re-auth buckets are reserved (section 10: per account and address, per account
   unless from a known device, per address; failures count, a success releases them).
2. The challenge is taken by the cookie in a transaction of its own that commits at once. It must
   be a ``reauth`` flow of this user **and this session**: another session's flow, or this
   session's after it rotated (another tab re-authenticated), is the generic failure.
3. The credential is looked up **with the session's user** (``find_own_credential``, section 7):
   another user's passkey is the generic failure, and its counter and ``last_used_at`` stay.
4. ``passkey_assertion`` verifies with no lock held and outside any transaction (counter 0 to the
   library); the backup-eligible flag must equal the stored one.
5. One write transaction (``passkey_reauth_session.complete``): ``lock_user`` first, the counter
   rule, ``rotate_session`` with ``reauth`` only (never ``stepup_at`` or ``mfa_at``), the
   ``reauth`` event. A session revoked meanwhile rolls it all back: 401 ``not_authenticated``.

Every other failure is 400 ``passkey_failed`` with no detail; the category goes to
``auth_events`` (``reauth_failed`` ``{method: "passkey", reason}``). The flow cookie is cleared on
every finish answer, the 429 of the buckets included.
"""

import secrets
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from classbeat.auth.cookies import (
    PASSKEY_COOKIES,
    clear_passkey_cookie,
    clear_session_cookie,
    set_passkey_cookie,
    set_session_cookie,
)
from classbeat.auth.deps import current_session, request_device_token, request_ip, require_user
from classbeat.auth.devices import is_known_device
from classbeat.auth.flowtools import factory, reserve
from classbeat.auth.middleware import rate_limited_response
from classbeat.auth.passkey_assertion import (
    AssertionRefused,
    authentication_options,
    credential_id_of,
    verify_assertion,
)
from classbeat.auth.passkey_policy import CHALLENGE_BYTES
from classbeat.auth.passkey_reauth_session import complete, find_own_credential, record_failure
from classbeat.auth.passkey_routes import FeatureOn
from classbeat.auth.passkey_store import create_challenge, credential_ids, take_challenge
from classbeat.auth.ratelimit import RateLimited, password_buckets
from classbeat.auth.sessions import SessionGone, SessionInfo
from classbeat.auth.userlock import lock_user, session_alive
from classbeat.config_passkeys import relying_party
from classbeat.context import UserContext
from classbeat.db import system_session
from classbeat.emailnorm import normalize_email
from classbeat.emails.notices import queue

router = APIRouter(prefix="/api/auth/passkeys/reauth", tags=["passkeys"])

PURPOSE = "reauth"
COOKIE = PASSKEY_COOKIES[PURPOSE]
NOT_AUTHENTICATED = {"reason": "not_authenticated"}


class FinishIn(BaseModel):
    credential: dict[str, Any]  # the ``PublicKeyCredential`` as JSON (``toJSON()``)


def _session(info: SessionInfo | None) -> SessionInfo:
    if info is None:  # require_user refused this already; kept for the type
        raise HTTPException(status_code=401, detail=NOT_AUTHENTICATED)
    return info


@router.post("/start")
async def reauth_start(
    _: FeatureOn,
    request: Request,
    response: Response,
    ctx: Annotated[UserContext, Depends(require_user)],
    current: Annotated[SessionInfo | None, Depends(current_session)],
) -> dict[str, Any]:
    info = _session(current)
    challenge = secrets.token_bytes(CHALLENGE_BYTES)
    async with system_session(factory(request)) as db:
        locked = await lock_user(db, ctx.user_id)
        if locked is None or locked.disabled or not await session_alive(db, ctx.user_id, info.id):
            raise HTTPException(status_code=401, detail=NOT_AUTHENTICATED)
        allow = await credential_ids(db, ctx.user_id)
        cookie = await create_challenge(
            db,
            purpose=PURPOSE,
            challenge=challenge,
            user_id=ctx.user_id,
            session_id=info.id,
            previous_cookie=request.cookies.get(COOKIE),
        )
    set_passkey_cookie(response, PURPOSE, cookie)
    rp = relying_party(request.app.state.settings)
    return {"publicKey": authentication_options(rp, challenge=challenge, allow=allow)}


def _answer(status: int, reason: str) -> JSONResponse:
    response = JSONResponse({"detail": {"reason": reason}}, status_code=status)
    clear_passkey_cookie(response, PURPOSE)
    return response


async def _failed(
    request: Request, user_id: UUID, reason: str, passkey: UUID | None = None
) -> JSONResponse:
    """The generic failure; the category goes to ``auth_events`` only."""
    async with system_session(factory(request)) as db:
        named = None if passkey is None else str(passkey)
        await record_failure(db, user_id, request_ip(request), reason, named)
    return _answer(400, "passkey_failed")


@router.post("/finish")
async def reauth_finish(
    _: FeatureOn,
    body: FinishIn,
    request: Request,
    background: BackgroundTasks,
    ctx: Annotated[UserContext, Depends(require_user)],
    current: Annotated[SessionInfo | None, Depends(current_session)],
) -> JSONResponse:
    info = _session(current)
    ip = request_ip(request)
    # Step 1: the password re-auth buckets (section 10).
    async with system_session(factory(request)) as db:
        known = await is_known_device(
            db, request.app.state.keys, ctx.user_id, request_device_token(request)
        )
    try:
        attempt = await reserve(
            request, password_buckets(normalize_email(info.email), ip, known_device=known)
        )
    except RateLimited as limited:
        refused = rate_limited_response(limited.retry_after)
        clear_passkey_cookie(refused, PURPOSE)
        return refused
    cookie = request.cookies.get(COOKIE)
    if not cookie:
        return await _failed(request, ctx.user_id, "challenge")
    # Step 2: the take commits on its own, before anything else runs (section 6, rule 1).
    async with system_session(factory(request)) as db:
        taken = await take_challenge(db, cookie)
    if (
        taken is None
        or taken.purpose != PURPOSE
        or taken.user_id != ctx.user_id
        or taken.session_id != info.id
    ):
        return await _failed(request, ctx.user_id, "challenge")
    # Step 3: the credential with the session's user, never by its id alone (section 7).
    try:
        credential_id = credential_id_of(body.credential)
    except AssertionRefused as refused:
        return await _failed(request, ctx.user_id, refused.reason)
    async with system_session(factory(request)) as db:
        found = await find_own_credential(db, ctx.user_id, credential_id)
    if found is None:
        return await _failed(request, ctx.user_id, "unknown")
    # Step 4: no transaction, no lock (section 6, rule 3).
    try:
        asserted = verify_assertion(
            relying_party(request.app.state.settings),
            body.credential,
            taken.challenge,
            found.public_key,
        )
    except AssertionRefused as refused:
        return await _failed(request, ctx.user_id, refused.reason, found.id)
    if asserted.backup_eligible != found.backup_eligible:
        return await _failed(request, ctx.user_id, "backup_flag", found.id)
    # Step 5: the one write transaction (``passkey_reauth_session``).
    try:
        outcome = await complete(request, info, found, asserted, ip)
    except SessionGone:
        gone = _answer(401, "not_authenticated")
        clear_session_cookie(gone)
        return gone
    queue(background, request, outcome.notice)
    if outcome.issued is None:
        return _answer(400, "passkey_failed")
    await attempt.succeed()
    response = JSONResponse({"status": "ok"})
    clear_passkey_cookie(response, PURPOSE)
    set_session_cookie(response, outcome.issued)
    return response
