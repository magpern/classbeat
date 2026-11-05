"""Origin and CSRF checks, ``no-store``, cookie clearing and our own access log.

ADR 0003 section 4: every ``POST``, ``PUT``, ``PATCH`` and ``DELETE`` passes two checks, and
because this is a middleware no route can forget them.

- ``Origin`` must equal the public origin. Without ``Origin``, ``Sec-Fetch-Site`` must be
  ``same-origin``; with neither, the request is refused.
- ``X-CSRF-Token`` must match ``HMAC(csrf_key, session.id)`` when the request carries a valid
  session, else the value of the ``__Host-classbeat_csrf`` cookie (double submit).

Section 9, amendment of 2026-11-05 (#577): every signed-in ``/api`` request takes a slot in the
general limit's buckets (``ratelimit.api_buckets``) before anything else runs, CSRF included, so
no route can forget it either; above a limit it is 429 ``rate_limited`` with ``Retry-After``.
Anonymous requests have the limits of their own routes (sign-in, sign-up, mail, OAuth).

Section 10: the access log replaces uvicorn's. It records method, path, status and time, never
the query string, headers, cookies or body.
"""

import hmac
import logging
import time

from fastapi import Request, Response
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint

from classbeat.auth.cookies import CSRF_COOKIE, SESSION_COOKIE, clear_session_cookie
from classbeat.auth.deps import request_ip, resolve_request
from classbeat.auth.keys import Keys, hmac_digest
from classbeat.auth.ratelimit import RateLimiter, api_buckets, api_limited
from classbeat.config import Settings

logger = logging.getLogger("classbeat.access")

SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


def csrf_token_for_session(keys: Keys, session_id: object) -> str:
    return hmac_digest(keys.csrf, str(session_id)).hex()


def check_origin(request: Request, settings: Settings) -> str | None:
    """The refusal reason, or ``None`` when the request comes from our own origin."""
    origin = request.headers.get("origin")
    if origin is not None:
        return None if origin == settings.public_base_url else "origin_mismatch"
    site = request.headers.get("sec-fetch-site")
    if site is None:
        return "origin_missing"
    return None if site == "same-origin" else "origin_mismatch"


async def check_csrf(request: Request, settings: Settings, keys: Keys) -> str | None:
    """The refusal reason, or ``None`` when the request may proceed."""
    if (reason := check_origin(request, settings)) is not None:
        return reason
    supplied = request.headers.get("x-csrf-token")
    if not supplied:
        return "csrf_token_missing"
    resolution = await resolve_request(request)
    if resolution.info is not None:
        expected = csrf_token_for_session(keys, resolution.info.id)
    else:
        expected = request.cookies.get(CSRF_COOKIE, "")
        if not expected:
            return "csrf_token_invalid"
    if not hmac.compare_digest(supplied.encode(), expected.encode()):
        return "csrf_token_invalid"
    return None


def rate_limited_response(retry_after: int) -> JSONResponse:
    """The 429 of every limit (``auth.setup``'s handler of ``RateLimited`` answers the same)."""
    return JSONResponse(
        {"detail": {"reason": "rate_limited"}},
        status_code=429,
        headers={"Retry-After": str(retry_after)},
    )


async def check_api_limit(request: Request) -> int | None:
    """The general limit (#577): ``Retry-After`` seconds when a signed-in ``/api`` request is over
    one of its buckets, else ``None``. Anonymous requests and the exempt paths are not counted."""
    if not api_limited(request.url.path):
        return None
    info = (await resolve_request(request)).info
    if info is None:
        return None
    limiter: RateLimiter = request.app.state.limiter
    attempt = await limiter.begin_together(
        api_buckets(
            request.app.state.settings,
            str(info.user_id),
            request_ip(request),
            request.method,
            request.url.path,
        )
    )
    return None if attempt.allowed else attempt.retry_after


class AuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        started = time.perf_counter()
        response: Response | None = None
        retry_after = await check_api_limit(request)
        if retry_after is not None:
            response = rate_limited_response(retry_after)
        if response is None and request.method not in SAFE_METHODS:
            reason = await check_csrf(request, request.app.state.settings, request.app.state.keys)
            if reason is not None:
                response = JSONResponse({"detail": {"reason": reason}}, status_code=403)
        if response is None:
            response = await call_next(request)
        if getattr(request.state, "clear_session_cookie", False) and not any(
            SESSION_COOKIE in value for value in response.headers.getlist("set-cookie")
        ):
            clear_session_cookie(response)
        if request.url.path.startswith("/api/") or request.url.path == "/api":
            response.headers["Cache-Control"] = "no-store"
        logger.info(
            "request",
            extra={
                "method": request.method,
                "path": request.url.path,
                "status": response.status_code,
                "duration_ms": round((time.perf_counter() - started) * 1000, 1),
            },
        )
        return response
