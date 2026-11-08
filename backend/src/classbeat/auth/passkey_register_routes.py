"""Add a passkey: ``POST /api/auth/passkeys/register/start`` and ``.../finish`` (ADR 0019
sections 4, 6 and 10, #772).

Both need ``CLASSBEAT_PASSKEYS`` (else 404 ``feature_off``, answered first), recent
authentication and, once the user's TOTP is confirmed, the step-up, plus CSRF and Origin (the
middleware). Finish checks them again rather than trusting start, so a step-up or re-auth that ran
out, or a move to a new network (#632 clears the times), fails closed with ``reauth_required`` or
``stepup_required``. Both count in the register buckets (every request).

Start locks the user row first (section 6) and checks its session is still there, then makes the
user handle if needed, stores a 32-byte challenge bound to this user and session and sets the
``register`` flow cookie (the previous flow of that cookie goes first). Refused with 409
``passkey_limit`` at 10 passkeys.

Finish, in three steps (section 6):

1. The challenge is taken by the cookie in a transaction of its own that commits at once. It must
   be a ``register`` flow of this user and this session; anything else is the generic
   ``passkey_failed``, and the cookie is cleared either way.
2. py_webauthn verifies the response (RP ID, origin, UV, the three algorithms) holding no lock and
   outside any transaction.
3. One transaction that locks the ``users`` row first, re-checks that the starting session still
   exists and the count is under 10, and stores the passkey for this user (never one the request
   names), with the ``passkey_added`` event. A reset that revoked the session meanwhile leaves
   nothing stored. A credential id that exists already (anyone's) is 409 ``passkey_exists``.

Then the account is mailed. Library errors never reach a log or a response (``passkey_webauthn``).
"""

import secrets
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from classbeat.auth.cookies import PASSKEY_COOKIES, clear_passkey_cookie, set_passkey_cookie
from classbeat.auth.deps import current_session, request_ip, require_recent_auth
from classbeat.auth.events import record_event
from classbeat.auth.flowtools import factory, reserve
from classbeat.auth.passkey_limits import register_buckets
from classbeat.auth.passkey_policy import CHALLENGE_BYTES, MAX_PASSKEYS, default_label
from classbeat.auth.passkey_routes import FeatureOn, Label, PasskeyOut
from classbeat.auth.passkey_store import (
    PasskeyRow,
    count_passkeys,
    create_challenge,
    credential_ids,
    ensure_user_handle,
    insert_passkey,
    take_challenge,
)
from classbeat.auth.passkey_webauthn import (
    PasskeyRefused,
    registration_options,
    verify_registration,
)
from classbeat.auth.sessions import SessionInfo
from classbeat.auth.userlock import lock_user, session_alive
from classbeat.config_passkeys import relying_party
from classbeat.context import UserContext
from classbeat.db import system_session
from classbeat.emails.notices import happened, queue

router = APIRouter(prefix="/api/auth/passkeys/register", tags=["passkeys"])

PURPOSE = "register"
COOKIE = PASSKEY_COOKIES[PURPOSE]


class FinishIn(BaseModel):
    credential: dict[str, Any]  # the ``PublicKeyCredential`` as JSON (``toJSON()``)
    label: Label | None = None


def _session(info: SessionInfo | None) -> SessionInfo:
    if info is None:  # require_recent_auth refused this already; kept for the type
        raise HTTPException(status_code=401, detail={"reason": "not_authenticated"})
    return info


def _refused(status: int, reason: str) -> JSONResponse:
    response = JSONResponse({"detail": {"reason": reason}}, status_code=status)
    clear_passkey_cookie(response, PURPOSE)
    return response


@router.post("/start")
async def register_start(
    _: FeatureOn,
    request: Request,
    response: Response,
    ctx: Annotated[UserContext, Depends(require_recent_auth)],
    current: Annotated[SessionInfo | None, Depends(current_session)],
) -> dict[str, Any]:
    info = _session(current)
    await reserve(request, register_buckets(str(ctx.user_id), request_ip(request)))
    challenge = secrets.token_bytes(CHALLENGE_BYTES)
    async with system_session(factory(request)) as db:
        # The user row first (section 6, #782 review N3): the previous flow's delete below locks
        # a challenge row that a reset's session delete cascades to, and the insert's foreign key
        # waits for the session row that reset is deleting. Under this lock the reset waits, or
        # has committed and the session check below stops the start.
        locked = await lock_user(db, ctx.user_id)
        if locked is None or locked.disabled or not await session_alive(db, ctx.user_id, info.id):
            raise HTTPException(status_code=401, detail={"reason": "not_authenticated"})
        if await count_passkeys(db, ctx.user_id) >= MAX_PASSKEYS:
            raise HTTPException(status_code=409, detail={"reason": "passkey_limit"})
        handle = await ensure_user_handle(db, ctx.user_id)
        if handle is None:
            raise HTTPException(status_code=401, detail={"reason": "not_authenticated"})
        exclude = await credential_ids(db, ctx.user_id)
        cookie = await create_challenge(
            db,
            purpose=PURPOSE,
            challenge=challenge,
            user_id=ctx.user_id,
            session_id=info.id,
            previous_cookie=request.cookies.get(COOKIE),
        )
    set_passkey_cookie(response, PURPOSE, cookie)
    options = registration_options(
        relying_party(request.app.state.settings),
        user_handle=handle,
        email=info.email,
        challenge=challenge,
        exclude=exclude,
    )
    return {"publicKey": options}


@router.post("/finish", status_code=201)
async def register_finish(
    _: FeatureOn,
    body: FinishIn,
    request: Request,
    background: BackgroundTasks,
    ctx: Annotated[UserContext, Depends(require_recent_auth)],
    current: Annotated[SessionInfo | None, Depends(current_session)],
) -> JSONResponse:
    info = _session(current)
    await reserve(request, register_buckets(str(ctx.user_id), request_ip(request)))
    cookie = request.cookies.get(COOKIE)
    if not cookie:
        return _refused(400, "passkey_failed")
    # Step 1: the take commits on its own, before anything else runs (section 6, rule 1).
    async with system_session(factory(request)) as db:
        taken = await take_challenge(db, cookie)
    if (
        taken is None
        or taken.purpose != PURPOSE
        or taken.user_id != ctx.user_id
        or taken.session_id != info.id
    ):
        return _refused(400, "passkey_failed")
    # Step 2: no transaction, no lock (section 6, rule 3).
    try:
        new = verify_registration(
            relying_party(request.app.state.settings), body.credential, taken.challenge
        )
    except PasskeyRefused:
        return _refused(400, "passkey_failed")
    # Step 3: the user row lock first, then the re-checks and the insert.
    label = body.label or default_label(datetime.now(UTC).date())
    row: PasskeyRow | None = None
    async with system_session(factory(request)) as db:
        locked = await lock_user(db, ctx.user_id)
        if locked is None or locked.disabled or not await session_alive(db, ctx.user_id, info.id):
            outcome = "gone"
        elif await count_passkeys(db, ctx.user_id) >= MAX_PASSKEYS:
            outcome = "limit"
        else:
            row = await insert_passkey(db, ctx.user_id, new, label)
            outcome = "exists" if row is None else "added"
            if row is not None:
                await record_event(
                    db,
                    "passkey_added",
                    user_id=ctx.user_id,
                    ip=request_ip(request),
                    detail={"passkey": str(row.id)},
                )
    if outcome == "gone":
        return _refused(400, "passkey_failed")
    if outcome == "limit":
        return _refused(409, "passkey_limit")
    if row is None:
        return _refused(409, "passkey_exists")
    queue(background, request, happened("passkey_added", info.email, ctx.user_id, label=label))
    response = JSONResponse(PasskeyOut.of(row).model_dump(mode="json"), status_code=201)
    clear_passkey_cookie(response, PURPOSE)
    return response
