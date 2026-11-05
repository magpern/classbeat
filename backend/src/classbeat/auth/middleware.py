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
the query string, headers, cookies or body. A signed-in ``/api`` request also gets a row in the
security log (``securitylog``; ADR 0003 amendment "security log", #632) once its status is known.

The session lookup, the limit and the CSRF check run here, outside the app's exception handlers,
so a database error in them gets the same redacted 500 here (``database_error_response``) instead
of reaching the server's traceback log with row values in its message (#632, #626 review).
"""

import hmac
import logging
import time

from fastapi import Request, Response
from fastapi.responses import JSONResponse
from sqlalchemy.exc import DBAPIError
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint

from classbeat.auth.cookies import CSRF_COOKIE, SESSION_COOKIE, clear_session_cookie
from classbeat.auth.deps import request_ip, resolve_request
from classbeat.auth.keys import Keys, hmac_digest
from classbeat.auth.ratelimit import RateLimiter, api_buckets, api_limited
from classbeat.auth.securitylog import entry_for, mark_counted, record
from classbeat.config import Settings
from classbeat.log import describe_database_error

logger = logging.getLogger("classbeat.access")
errors = logging.getLogger("classbeat.auth.setup")

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


def database_error_response(exc: DBAPIError) -> JSONResponse:
    """The 500 of a database error, logged by class and SQLSTATE only: the message carries row
    values (emails, hashes). ``auth.setup``'s handler of ``DBAPIError`` answers the same."""
    errors.error("database error: %s", describe_database_error(exc))
    return JSONResponse({"detail": "Internal Server Error"}, status_code=500)


async def check_api_limit(request: Request) -> int | None:
    """The general limit (#577): ``Retry-After`` seconds when a signed-in ``/api`` request is over
    one of its buckets, else ``None``. Anonymous requests and the exempt paths are not counted.

    A request the limiter counted (allowed or not) is marked for the security log here and only
    here (``securitylog.mark_counted``): the log writes a row for exactly those requests, so its
    rows never exceed the limiter's budget (plus one 429 row per user and minute) and a path
    the limiter exempts is never logged (#632, ADR 0003 amendment "the security log")."""
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
    mark_counted(request)
    return None if attempt.allowed else attempt.retry_after


class AuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        started = time.perf_counter()
        try:
            response = await self._checks(request)
        except DBAPIError as exc:
            response = database_error_response(exc)
        if response is None:
            response = await call_next(request)
        if getattr(request.state, "clear_session_cookie", False) and not any(
            SESSION_COOKIE in value for value in response.headers.getlist("set-cookie")
        ):
            clear_session_cookie(response)
        if request.url.path.startswith("/api/") or request.url.path == "/api":
            response.headers["Cache-Control"] = "no-store"
        # The security log (#632): a row per request the general limit counted, its own refusals
        # here (429, 403) included; an exempt, anonymous or non-API request writes none.
        entry = entry_for(request, response.status_code)
        if entry is not None:
            await record(request.app.state.session_factory, entry)
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

    @staticmethod
    async def _checks(request: Request) -> Response | None:
        """The general limit, then Origin and CSRF: the refusal, or ``None`` to go on."""
        retry_after = await check_api_limit(request)
        if retry_after is not None:
            return rate_limited_response(retry_after)
        if request.method not in SAFE_METHODS:
            reason = await check_csrf(request, request.app.state.settings, request.app.state.keys)
            if reason is not None:
                return JSONResponse({"detail": {"reason": reason}}, status_code=403)
        return None
