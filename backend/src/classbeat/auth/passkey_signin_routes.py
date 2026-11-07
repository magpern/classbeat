"""Username-less passkey sign-in: ``POST /api/auth/passkeys/signin/start`` and ``.../finish``
(ADR 0019 sections 5, 6, 7 and 10, #773).

Anonymous, with the CSRF double-submit and the Origin check of the password form (the
middleware). Both answer 404 ``feature_off`` first while ``CLASSBEAT_PASSKEYS`` is off.

**Start** takes no email and answers the same options to everyone (``passkey_assertion``). It
counts in the start buckets, reserved one at a time and stopping at the first refusal
(``passkey_limits.start_buckets``): the address and the IPv6 /48 answer 429 ``rate_limited``, the
global bucket 503 ``passkey_busy``, both with ``Retry-After``. Password and provider sign-in use
other buckets. It stores a 32-byte challenge with no user or session and sets the ``sign_in`` flow
cookie (the browser's previous sign-in flow goes first).

**Finish**, in the order of section 5:

1. The failure bucket per address is reserved (429 above it), then the challenge is taken by the
   cookie in a transaction of its own that commits at once; it must be a ``sign_in`` flow.
2. The credential is found by its id. Unknown is 400 ``passkey_unknown`` (the SPA can tell the
   browser to stop offering it). Then the failure bucket of its user, unless from a known device.
3. ``userHandle`` must be present and equal the user's handle.
4. ``passkey_assertion`` verifies with no lock held and outside any transaction (counter 0 to the
   library); the backup-eligible flag must equal the stored one.
5. One write transaction (``passkey_signin_session.complete``): ``lock_user`` first, the
   counter rule, the disabled check, the ``ADMIN_EMAILS`` grant and ``start_session``. A reset
   that commits first leaves the credential gone and no session; one that comes later waits for
   this commit and revokes it.

Every failure but ``passkey_unknown`` is 400 ``passkey_failed`` with no detail; the category goes
to ``auth_events`` (``sign_in_failed`` ``{method: "passkey", reason}``). The flow cookie is
cleared on every finish. An admin's role still takes effect only after TOTP in the new session
(``start_session`` never sets ``mfa_at``).
"""

import secrets
from collections.abc import Sequence
from typing import Any
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from classbeat.auth.cookies import (
    PASSKEY_COOKIES,
    clear_passkey_cookie,
    set_device_cookie,
    set_passkey_cookie,
    set_session_cookie,
)
from classbeat.auth.deps import request_device_token, request_ip
from classbeat.auth.devices import is_known_device
from classbeat.auth.flowtools import factory, limiter, reserve
from classbeat.auth.passkey_assertion import (
    AssertionRefused,
    authentication_options,
    credential_id_of,
    user_handle_matches,
    verify_assertion,
)
from classbeat.auth.passkey_limits import (
    StartBucket,
    failure_account_buckets,
    failure_ip_buckets,
    start_buckets,
)
from classbeat.auth.passkey_policy import CHALLENGE_BYTES
from classbeat.auth.passkey_routes import FeatureOn
from classbeat.auth.passkey_signin_session import complete, record_failure
from classbeat.auth.passkey_signin_store import find_credential
from classbeat.auth.passkey_store import create_challenge, take_challenge
from classbeat.auth.ratelimit import Attempt, RateLimited, RateLimiter
from classbeat.auth.signin_routes import SignedIn
from classbeat.config_passkeys import relying_party
from classbeat.db import system_session
from classbeat.emails.notices import queue

router = APIRouter(prefix="/api/auth/passkeys/signin", tags=["passkeys"])

PURPOSE = "sign_in"
COOKIE = PASSKEY_COOKIES[PURPOSE]


class FinishIn(BaseModel):
    credential: dict[str, Any]  # the ``PublicKeyCredential`` as JSON (``toJSON()``)


async def reserve_in_order(
    rate_limiter: RateLimiter, items: Sequence[StartBucket]
) -> tuple[StartBucket, Attempt] | None:
    """Reserve ``items`` narrowest first, one ``begin`` each, stopping at the first refusal, so a
    refused start takes no slot in a wider bucket (section 10). The refusing bucket and its
    attempt, or None when every bucket allowed the start."""
    for item in items:
        attempt = await rate_limiter.begin([item.bucket])
        if not attempt.allowed:
            return item, attempt
    return None


@router.post("/start")
async def signin_start(_: FeatureOn, request: Request, response: Response) -> dict[str, Any]:
    refused = await reserve_in_order(limiter(request), start_buckets(request_ip(request)))
    if refused is not None:
        item, attempt = refused
        if item.busy:
            raise HTTPException(
                status_code=503,
                detail={"reason": "passkey_busy"},
                headers={"Retry-After": str(attempt.retry_after)},
            )
        raise RateLimited(attempt.retry_after)
    challenge = secrets.token_bytes(CHALLENGE_BYTES)
    async with system_session(factory(request)) as db:
        cookie = await create_challenge(
            db,
            purpose=PURPOSE,
            challenge=challenge,
            user_id=None,
            session_id=None,
            previous_cookie=request.cookies.get(COOKIE),
        )
    set_passkey_cookie(response, PURPOSE, cookie)
    options = authentication_options(relying_party(request.app.state.settings), challenge=challenge)
    return {"publicKey": options}


def _answer(status: int, reason: str) -> JSONResponse:
    response = JSONResponse({"detail": {"reason": reason}}, status_code=status)
    clear_passkey_cookie(response, PURPOSE)
    return response


async def _failed(request: Request, user_id: UUID | None, reason: str) -> JSONResponse:
    """The generic failure; the category goes to ``auth_events`` only."""
    async with system_session(factory(request)) as db:
        await record_failure(db, user_id, request_ip(request), reason)
    return _answer(400, "passkey_failed")


@router.post("/finish")
async def signin_finish(
    _: FeatureOn, body: FinishIn, request: Request, background: BackgroundTasks
) -> JSONResponse:
    ip = request_ip(request)
    by_ip = await reserve(request, failure_ip_buckets(ip))
    cookie = request.cookies.get(COOKIE)
    if not cookie:
        return await _failed(request, None, "challenge")
    # Step 1: the take commits on its own, before anything else runs (section 6, rule 1).
    async with system_session(factory(request)) as db:
        taken = await take_challenge(db, cookie)
    if taken is None or taken.purpose != PURPOSE:
        return await _failed(request, None, "challenge")
    # Step 2: the credential by its id alone (no user is known yet).
    try:
        credential_id = credential_id_of(body.credential)
    except AssertionRefused as refused:
        return await _failed(request, None, refused.reason)
    keys = request.app.state.keys
    async with system_session(factory(request)) as db:
        found = await find_credential(db, credential_id)
        known = found is not None and await is_known_device(
            db, keys, found.user_id, request_device_token(request)
        )
    if found is None:
        return _answer(400, "passkey_unknown")
    by_account = await reserve(
        request, failure_account_buckets(str(found.user_id), known_device=known)
    )
    # Step 3 and 4: no transaction, no lock (section 6, rule 3).
    if not user_handle_matches(body.credential, found.user_handle):
        return await _failed(request, found.user_id, "user_handle")
    try:
        asserted = verify_assertion(
            relying_party(request.app.state.settings),
            body.credential,
            taken.challenge,
            found.public_key,
        )
    except AssertionRefused as refused:
        return await _failed(request, found.user_id, refused.reason)
    if asserted.backup_eligible != found.backup_eligible:
        return await _failed(request, found.user_id, "backup_flag")
    # Step 5: the one write transaction (``passkey_signin_session``).
    outcome = await complete(request, found, asserted, ip)
    queue(background, request, outcome.notice)
    if outcome.issued is None or outcome.user is None:
        return _answer(400, "passkey_failed")
    await by_ip.succeed()
    await by_account.succeed()
    user = outcome.user
    signed_in = SignedIn(id=user.id, email=user.email, role=user.role)
    response = JSONResponse(signed_in.model_dump(mode="json"))
    clear_passkey_cookie(response, PURPOSE)
    set_session_cookie(response, outcome.issued)
    set_device_cookie(response, outcome.device)
    return response
