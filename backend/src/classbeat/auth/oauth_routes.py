"""OAuth sign-in, linking and re-authentication (ADR 0003 section 5).

``POST /api/auth/oauth/{provider}/start`` (CSRF-checked) creates the single-use flow and returns
the authorization URL. ``GET /oauth/{provider}/callback`` takes the flow atomically *before* the
code is exchanged, checks provider and state, and answers with a redirect: the outcome is a short
code in the query of an SPA page, never anything the provider sent. This route is registered
before the SPA catch-all; its query string (code, state) is never logged.
"""

import logging
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, Response
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field

from classbeat.auth.clientip import bucket_ip
from classbeat.auth.cookies import (
    OAUTH_COOKIE,
    PENDING_COOKIE,
    clear_oauth_cookie,
    set_device_cookie,
    set_oauth_cookie,
    set_pending_cookie,
    set_session_cookie,
)
from classbeat.auth.deps import (
    current_context,
    current_session,
    request_device_token,
    request_ip,
    request_session_token,
    require_recent_auth,
    require_user,
)
from classbeat.auth.events import record_event
from classbeat.auth.flowtools import factory, reserve, user_agent
from classbeat.auth.oauth_client import OAuthRuntime, ProviderError, authorization_url, identify
from classbeat.auth.oauth_outcomes import Outcome, apply
from classbeat.auth.oauth_outcomes import Request as OutcomeRequest
from classbeat.auth.oauth_providers import GOOGLE, visible_providers
from classbeat.auth.oauth_store import Flow, create_flow, has_password, safe_return_to, take_flow
from classbeat.auth.ratelimit import OAUTH_IP, Bucket
from classbeat.auth.sessions import SessionInfo
from classbeat.auth.signup_routes import valid_invite
from classbeat.db import system_session
from classbeat.emails.notices import queue
from classbeat.flags import effective as flags

logger = logging.getLogger(__name__)
router = APIRouter(tags=["auth"])

FIELD_MAX = 1024
# The ``error`` values of RFC 6749 4.1.2.1 and OpenID Connect Core 3.1.2.6. Only these are
# logged; anything else is "unrecognized" (the query is the sender's text, never logged).
OAUTH_ERROR_CODES = frozenset(
    {
        "invalid_request",
        "unauthorized_client",
        "access_denied",
        "unsupported_response_type",
        "invalid_scope",
        "server_error",
        "temporarily_unavailable",
        "interaction_required",
        "login_required",
        "account_selection_required",
        "consent_required",
        "invalid_request_uri",
        "invalid_request_object",
        "request_not_supported",
        "request_uri_not_supported",
        "registration_not_supported",
    }
)


class StartIn(BaseModel):
    # ``sign_in`` (the sign-in screen) never creates an account; only ``sign_up`` (the sign-up
    # screen) may, and only it carries an invite (#617).
    purpose: Literal["sign_in", "sign_up", "link", "reauth"] = "sign_in"
    invite_code: str | None = Field(default=None, max_length=FIELD_MAX)
    return_to: str | None = Field(default=None, max_length=FIELD_MAX)


class StartOut(BaseModel):
    authorization_url: str


class ProvidersOut(BaseModel):
    providers: list[str]


def _runtime(request: Request) -> OAuthRuntime:
    return request.app.state.oauth


def _redirect_uri(request: Request, provider: str) -> str:
    return f"{request.app.state.settings.public_base_url}/oauth/{provider}/callback"


@router.get("/api/auth/oauth/providers")
async def enabled(request: Request) -> ProvidersOut:
    # Only the providers the sign-in and sign-up screens show; the account page lists every
    # enabled one through ``GET /api/auth/identities``.
    settings = request.app.state.settings
    return ProvidersOut(providers=visible_providers(settings, _runtime(request).providers))


@router.post("/api/auth/oauth/{provider}/start")
async def start(
    provider: str,
    body: StartIn,
    request: Request,
    response: Response,
    info: Annotated[SessionInfo | None, Depends(current_session)],
) -> StartOut:
    prov = _runtime(request).providers.get(provider)
    if prov is None:
        raise HTTPException(status_code=404, detail={"reason": "provider_disabled"})
    await reserve(request, [Bucket(OAUTH_IP, bucket_ip(request_ip(request)))])
    user_id = None
    if body.purpose == "link":
        # Recent auth (and step-up with a confirmed TOTP) before the start (section 6).
        ctx = await require_user(await current_context(request, info))
        await require_recent_auth(ctx, info)
        user_id = ctx.user_id
    elif body.purpose == "reauth":
        ctx = await require_user(await current_context(request, info))
        user_id = ctx.user_id
        if provider == GOOGLE:
            # #687: Google's answer carries no fresh auth_time, so it re-authenticates only an
            # account without a password; one with a password confirms with it. The callback
            # checks this again under the user lock (``oauth_outcomes._freshness``).
            async with system_session(factory(request)) as db:
                if await has_password(db, user_id):
                    raise HTTPException(status_code=403, detail={"reason": "reauth_use_password"})
    invite_id: UUID | None = None
    settings = request.app.state.settings
    if body.purpose == "sign_up" and body.invite_code:
        async with system_session(factory(request)) as db:
            invite_mode = await flags.signup_mode(db, settings.signup_mode) == "invite"
            if invite_mode:
                invite_id = await valid_invite(db, body.invite_code)
        if invite_mode and invite_id is None:
            raise HTTPException(status_code=400, detail={"reason": "invalid_invite"})
    async with system_session(factory(request)) as db:
        flow = await create_flow(
            db,
            provider=provider,
            purpose=body.purpose,
            oidc=prov.oidc,
            user_id=user_id,
            invite_id=invite_id,
            return_to=safe_return_to(body.return_to),
        )
    set_oauth_cookie(response, flow.cookie)
    return StartOut(
        authorization_url=authorization_url(
            prov,
            _redirect_uri(request, provider),
            state=flow.state,
            nonce=flow.nonce,
            verifier=flow.verifier,
            reauth=body.purpose == "reauth",
        )
    )


def _target(flow: Flow | None, outcome: Outcome) -> str:
    purpose = flow.purpose if flow else "sign_in"
    if outcome.code != "ok":
        if purpose in ("link", "reauth"):
            base = "/account"
        elif purpose == "sign_up":
            base = "/signup"  # the screen the flow started on, with its invite field (#617)
        else:
            base = "/signin"
        if outcome.code == "no_account" and flow is not None:
            # Which provider account has none, for the page's sentence (#617). ``flow.provider``
            # is one of the three names the table's check allows, never anything a client sent.
            return f"{base}?oauth=no_account&provider={flow.provider}"
        return f"{base}?oauth={outcome.code}"
    return_to = safe_return_to(flow.return_to if flow else None)  # checked again before use
    match outcome.next:
        case "signed_in":
            return return_to or "/"
        case "reauthed":
            return return_to or "/account?oauth=reauthed"
        case "linked":
            return "/account?oauth=linked"
        case _:
            return "/signin?oauth=email_needed"


async def _handle(
    request: Request, provider: str, flow: Flow | None, info: SessionInfo | None
) -> Outcome:
    query = request.query_params
    state, code = query.get("state", ""), query.get("code")
    if flow is None:
        return Outcome("invalid", why="no_flow")  # no cookie, expired, or already used
    if flow.provider != provider:
        return Outcome("invalid", why="provider_mismatch")
    if not flow.state_matches(state):
        return Outcome("invalid", why="state_mismatch")
    error = query.get("error")
    if error:
        named = error if error in OAUTH_ERROR_CODES else "unrecognized"
        return Outcome("denied", why=f"provider_error={named}")
    if not code:
        return Outcome("denied", why="no_code")
    if flow.purpose in ("link", "reauth") and (info is None or info.user_id != flow.user_id):
        return Outcome("session_mismatch", why="no_session" if info is None else "other_user")
    runtime = _runtime(request)
    try:
        identity = await identify(
            runtime,
            runtime.providers[provider],
            _redirect_uri(request, provider),
            code=code,
            verifier=flow.verifier,
            nonce=flow.nonce,
        )
    except ProviderError as exc:
        # The text names the failed check, never a token or body (``ProviderError``).
        async with system_session(factory(request)) as db:
            await record_event(
                db,
                "oauth_failed",
                user_id=flow.user_id,
                ip=request_ip(request),
                detail={"provider": provider},
            )
        return Outcome("invalid", why=f"provider: {exc}")
    async with system_session(factory(request)) as db:
        return await apply(
            db,
            OutcomeRequest(
                settings=request.app.state.settings,
                keys=request.app.state.keys,
                flow=flow,
                identity=identity,
                info=info,
                session_token=request_session_token(request),
                device_token=request_device_token(request),
                pending_token=request.cookies.get(PENDING_COOKIE),
                ip=request_ip(request),
                user_agent=user_agent(request),
            ),
        )


@router.get("/oauth/{provider}/callback")
async def callback(
    provider: str,
    request: Request,
    background: BackgroundTasks,
    info: Annotated[SessionInfo | None, Depends(current_session)],
) -> Response:
    if provider not in _runtime(request).providers:
        raise HTTPException(status_code=404)
    await reserve(request, [Bucket(OAUTH_IP, bucket_ip(request_ip(request)))])
    cookie = request.cookies.get(OAUTH_COOKIE)
    flow = None
    if cookie:
        async with system_session(factory(request)) as db:
            flow = await take_flow(db, cookie)  # before the code is exchanged: single use
    outcome = await _handle(request, provider, flow, info)
    if outcome.code != "ok":
        # One line for every refused callback (#522): before it, only a provider error was
        # logged and the rest sat in auth_events alone. Provider, purpose, outcome and why only.
        logger.warning(
            "oauth callback refused: provider=%s purpose=%s outcome=%s why=%s",
            provider,
            flow.purpose if flow else "unknown",
            outcome.code,
            outcome.why or outcome.code,
        )
    response = RedirectResponse(_target(flow, outcome), status_code=303)
    clear_oauth_cookie(response)
    response.headers["Cache-Control"] = "no-store"
    response.headers["Referrer-Policy"] = "no-referrer"
    if outcome.issued is not None:
        set_session_cookie(response, outcome.issued)
    if outcome.device_token is not None:
        set_device_cookie(response, outcome.device_token)
    if outcome.new_pending_token is not None:
        set_pending_cookie(response, outcome.new_pending_token)
    queue(background, request, outcome.notice)
    return response
