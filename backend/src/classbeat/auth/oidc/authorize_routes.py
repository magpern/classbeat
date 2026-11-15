"""``GET /oidc/authorize``, ``GET /oidc/authorize/continue`` and ``POST /oidc/authorize/consent``
(ADR 0034 section 1).

- **authorize** validates the request (``request_policy``): a bad client or redirect address is
  an error page and never a redirect; any other problem redirects to the registered address with
  ``invalid_request``. A valid request is stored, bound to the browser's flow cookie (kept, or
  set; its ``Max-Age`` is renewed either way), and the browser goes on to continue.
- **continue** (``?r=<id>``, a relative ``return_to`` after sign-in) needs the row **and** a flow
  cookie whose hash is its binding. A missing, unknown, expired or mismatched request is the
  "expired" page: never a redirect, an ``oidc_request_refused`` event, nothing deleted. A
  signed-out user is sent to sign in and back. Then ``advance``: a code, or the consent page.
- **consent** (JSON ``{"r", "allow"}``, CSRF and Origin checked by the middleware like every
  POST) checks the binding again and answers ``{"redirect": <client address>}``.
"""

from typing import Annotated
from urllib.parse import quote
from uuid import UUID

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, ConfigDict
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from classbeat.auth.deps import current_session, request_ip, require_user
from classbeat.auth.events import record_event
from classbeat.auth.flowtools import reserve
from classbeat.auth.oidc.advance import RequestGone, advance
from classbeat.auth.oidc.flow import (
    EXPIRED_PAGE,
    INVALID_PAGE,
    NO_STORE,
    browser_hash,
    client_redirect,
    flow_cookie,
    keep_or_new_cookie,
    page,
    provider_of,
    redirect,
    set_flow_cookie,
)
from classbeat.auth.oidc.limits import (
    authorize_ip_buckets,
    authorize_user_buckets,
    consent_buckets,
)
from classbeat.auth.oidc.request_policy import AuthorizeRequest, ErrorPage, check_authorize
from classbeat.auth.oidc.request_store import create_request, find_request
from classbeat.context import UserContext
from classbeat.db import system_session

router = APIRouter(include_in_schema=False)
CONSENT_PAGE = "/player-consent"
EXPIRED_REASON = "request_expired"


def _factory(request: Request) -> async_sessionmaker[AsyncSession]:
    return request.app.state.session_factory


def _continue_path(request_id: UUID) -> str:
    return f"/oidc/authorize/continue?r={request_id}"


def _request_id(value: str | None) -> UUID | None:
    try:
        return UUID(value) if value and len(value) == 36 else None
    except ValueError:
        return None


async def _refused(request: Request) -> None:
    async with system_session(_factory(request)) as db:
        await record_event(db, "oidc_request_refused", ip=request_ip(request))


@router.get("/oidc/authorize")
async def authorize(request: Request) -> Response:
    provider = provider_of(request)
    await reserve(request, authorize_ip_buckets(request_ip(request)))
    params: dict[str, list[str]] = {}
    for name, value in request.query_params.multi_items():
        params.setdefault(name, []).append(value)
    outcome = check_authorize(params, provider.client)
    if isinstance(outcome, ErrorPage):
        return page(INVALID_PAGE)
    if not isinstance(outcome, AuthorizeRequest):
        extra = {} if outcome.state is None else {"state": outcome.state}
        return redirect(client_redirect(provider.client.redirect_uri, error=outcome.error, **extra))
    cookie = keep_or_new_cookie(request)
    async with system_session(_factory(request)) as db:
        request_id = await create_request(db, browser_hash(cookie), outcome)
    response = redirect(_continue_path(request_id))
    set_flow_cookie(response, cookie)  # set, or kept with its Max-Age renewed (#1056 N16)
    return response


@router.get("/oidc/authorize/continue")
async def continue_request(request: Request, r: str | None = None) -> Response:
    provider = provider_of(request)
    cookie, request_id = flow_cookie(request), _request_id(r)
    if cookie is None or request_id is None:
        await _refused(request)
        return page(EXPIRED_PAGE)
    binding = browser_hash(cookie)
    async with system_session(_factory(request)) as db:
        stored = await find_request(db, request_id, binding)
    if stored is None:
        await _refused(request)
        return page(EXPIRED_PAGE)
    info = await current_session(request)
    if info is None:
        return redirect(f"/signin?return_to={quote(_continue_path(request_id), safe='')}")
    await reserve(request, authorize_user_buckets(str(info.user_id)))
    settings = request.app.state.settings
    try:
        async with system_session(_factory(request)) as db:
            location = await advance(
                db, provider, info, stored, binding,
                allow=None, passkeys=settings.passkeys, ip=request_ip(request),
            )  # fmt: skip
    except RequestGone:
        await _refused(request)
        return page(EXPIRED_PAGE)
    if location == "consent":
        return redirect(f"{CONSENT_PAGE}?r={request_id}")
    return redirect(location)


class ConsentIn(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    r: str
    allow: bool


def _expired() -> JSONResponse:
    return JSONResponse({"detail": {"reason": EXPIRED_REASON}}, status_code=400, headers=NO_STORE)


@router.post("/oidc/authorize/consent")
async def consent(
    body: ConsentIn,
    request: Request,
    ctx: Annotated[UserContext, Depends(require_user)],
) -> JSONResponse:
    provider = provider_of(request)
    await reserve(request, consent_buckets(str(ctx.user_id)))
    info = await current_session(request)
    cookie, request_id = flow_cookie(request), _request_id(body.r)
    if info is None or cookie is None or request_id is None:
        await _refused(request)
        return _expired()
    binding = browser_hash(cookie)
    settings = request.app.state.settings
    try:
        async with system_session(_factory(request)) as db:
            stored = await find_request(db, request_id, binding)
            if stored is None:
                raise RequestGone
            location = await advance(
                db, provider, info, stored, binding,
                allow=body.allow, passkeys=settings.passkeys, ip=request_ip(request),
            )  # fmt: skip
    except RequestGone:
        await _refused(request)
        return _expired()
    return JSONResponse({"redirect": location}, headers=NO_STORE)
