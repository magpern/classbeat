"""The admin router shell (ADR 0011 section 6): every ``/api/admin`` route of the admin area goes
through ``admin_gate``; the feature modules (#218, #220, #221, #222) only add routes to the router
``admin_router()`` returns. ``tests/test_admin_routes_guard.py`` enumerates the app's routes and
fails for one under ``/api/admin`` without the gate (``whoami`` of M1 needs ``require_admin``).

The gate, in this order:

1. ``require_admin`` (ADR 0003 section 7): 401 without a session; 403 ``forbidden`` for a caller
   whose role is not ``admin`` (no audit row, no maint connection); 403 ``mfa_required`` for an
   admin without TOTP in the last 12 hours.
2. ``require_stepup`` for every method but GET and HEAD: 403 ``stepup_required`` without a TOTP or
   recovery code in the last 5 minutes. CSRF and Origin are checked for those methods by the auth
   middleware before any route runs.
3. 503 ``admin_unavailable`` when web has no maint credentials (``AdminDb`` is ``None``).
4. 60 requests per admin and minute (``auth.admin_buckets``): 429 ``rate_limited``.

An admin refused at 1 (``mfa_required``), 2 or 4 gets an ``admin.request`` audit row with
``outcome = 'refused'``, within the limiter's 20 per admin and 100 per minute. So does an admin
past the gate whose request is then refused for its input (ADR 0011 section 10, #227): FastAPI's
own 422 (reason ``invalid_input``) or an ``AdminRefused`` raised before any ``admin_session``
(its own reason, ``period_invalid`` say). A body that is not JSON is refused by FastAPI before any
dependency runs; the gate then runs in the validation handler first, so a non-admin still gets
the 403 and an admin's 422 still has its row. Every response carries ``Cache-Control: no-store``.
"""

import re
import secrets
from typing import Annotated, Any, NoReturn

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute, iter_route_contexts

from classbeat import auth
from classbeat.admin.audit import METHOD_PATTERN, ROUTE_PATTERN
from classbeat.admin.db import Actor, AdminDb, AdminRefused, audit_refusal
from classbeat.context import UserContext

GATE_ACTION = "admin.request"
SAFE_METHODS = frozenset({"GET", "HEAD"})


def _refuse(status: int, reason: str) -> NoReturn:
    raise HTTPException(status_code=status, detail={"reason": reason})


def _ip_hash(request: Request) -> bytes:
    keys: auth.Keys = request.app.state.keys
    return auth.hmac_digest(
        keys.rate_limit, "admin-audit-ip", auth.bucket_ip(auth.request_ip(request))
    )


def _gate_params(request: Request) -> dict[str, str]:
    """The ``admin.request`` params: the route template and method, or ``/api/admin`` and
    ``OTHER`` when one would not pass ``RequestParams`` (a camelCase ``{userId}``, say), so a
    refusal keeps its status and its row instead of turning into a 500."""
    route: Any = request.scope.get("route")
    path = getattr(route, "path", None)
    method = request.method
    return {
        "method": method if re.fullmatch(METHOD_PATTERN, method) else "OTHER",
        "route": path
        if isinstance(path, str) and re.fullmatch(ROUTE_PATTERN, path)
        else "/api/admin",
    }


def admin_db(request: Request) -> AdminDb | None:
    return getattr(request.app.state, "admin_db", None)


async def admin_gate(
    request: Request,
    response: Response,
    info: Annotated[auth.SessionInfo | None, Depends(auth.current_session)],
    ctx: Annotated[UserContext | None, Depends(auth.current_context)],
) -> Actor:
    """The admin area's one dependency (module docstring); returns the audited ``Actor``."""
    response.headers["Cache-Control"] = "no-store"
    if ctx is None or info is None:
        _refuse(401, "not_authenticated")
    if info.stored_role != "admin":
        _refuse(403, "forbidden")  # not an admin: no audit row and no maint connection
    db = admin_db(request)
    actor = Actor(
        user_id=info.user_id,
        email=info.email,
        request_id=getattr(request.state, "request_id", None) or secrets.token_hex(16),
        ip_hash=_ip_hash(request),
    )

    async def refused(status: int, reason: str, headers: dict[str, str] | None = None) -> NoReturn:
        if db is not None:
            await audit_refusal(
                db, actor, GATE_ACTION, reason,
                params=_gate_params(request),
            )  # fmt: skip
        raise HTTPException(status_code=status, detail={"reason": reason}, headers=headers)

    if ctx.role != "admin":
        await refused(403, "mfa_required")
    if request.method not in SAFE_METHODS and not info.recent_stepup:
        await refused(403, "stepup_required")
    if db is None:
        _refuse(503, "admin_unavailable")
    attempt = await request.app.state.limiter.begin(auth.admin_buckets(str(info.user_id)))
    if not attempt.allowed:
        await refused(429, "rate_limited", {"Retry-After": str(attempt.retry_after)})
    request.state.admin_actor = actor  # past the gate: an input refusal is audited (install)
    return actor


AdminActor = Annotated[Actor, Depends(admin_gate)]


def admin_router() -> APIRouter:
    """A router under ``/api/admin`` whose every route depends on ``admin_gate``."""
    return APIRouter(prefix="/api/admin", tags=["admin"], dependencies=[Depends(admin_gate)])


# The admin area's routes; the feature modules add theirs to it.
router = admin_router()


async def _audit_input_refusal(request: Request, reason: str) -> None:
    """An ``admin.request`` ``refused`` row for a request that passed the gate and was then
    refused for its input (ADR 0011 section 10, #227): FastAPI's own 422 or an
    ``AdminRefused`` raised outside an ``admin_session``. A request the gate refused has no
    actor here (the gate wrote its own row), and nothing is written without maint."""
    actor = getattr(request.state, "admin_actor", None)
    db = admin_db(request)
    if isinstance(actor, Actor) and db is not None:
        await audit_refusal(db, actor, GATE_ACTION, reason, params=_gate_params(request))


async def _gate_before_validation(request: Request) -> JSONResponse | None:
    """FastAPI refuses a body that is not JSON before any dependency runs, so ``admin_gate``
    would not have run: a non-admin would get the 422 (learning that the route takes a JSON
    body) instead of the 403, and an admin's refusal would have no row (#303 review). The gate
    runs here then, with its own refusals and rows; ``None`` once it passed (the actor is set)."""
    route: Any = request.scope.get("route")
    if getattr(request.state, "admin_actor", None) is not None or not isinstance(route, APIRoute):
        return None
    if admin_gate not in _calls(route.dependant):
        return None  # ``whoami``: not the admin area's gate
    info = await auth.current_session(request)
    ctx = await auth.current_context(request, info)
    try:
        await admin_gate(request, Response(), info, ctx)
    except HTTPException as exc:
        return JSONResponse(
            {"detail": exc.detail},
            status_code=exc.status_code,
            headers={**(exc.headers or {}), "Cache-Control": "no-store"},
        )
    return None


def install(app: Any) -> None:
    """The admin router, the feature modules' routers, the handler that turns an
    ``AdminRefused`` into its response, and the audit row of an admin request's 422. Call it
    after ``auth.install``: it wraps the validation handler installed there."""
    # The feature modules import this one.
    from classbeat.admin import audit_routes, codes, jobs, usage, users

    app.include_router(router)
    # Each feature module's own ``admin_router()``.
    for feature in (users.router, audit_routes.router, usage.router, jobs.router, codes.router):
        app.include_router(feature)

    previous = app.exception_handlers.get(
        RequestValidationError, request_validation_exception_handler
    )

    @app.exception_handler(RequestValidationError)
    async def validation_failed(request: Request, exc: Exception) -> Response:
        if not request.url.path.startswith("/api/admin"):
            return await previous(request, exc)
        refused = await _gate_before_validation(request)
        if refused is not None:
            return refused
        await _audit_input_refusal(request, "invalid_input")
        response = await previous(request, exc)
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.exception_handler(AdminRefused)
    async def admin_refused(request: Request, exc: Exception) -> JSONResponse:
        status, reason = (
            (exc.status, exc.reason) if isinstance(exc, AdminRefused) else (409, "refused")
        )
        if isinstance(exc, AdminRefused) and not exc.audited:
            await _audit_input_refusal(request, reason)
        return JSONResponse(
            {"detail": {"reason": reason}},
            status_code=status,
            headers={"Cache-Control": "no-store"},
        )


def admin_area_routes(routes: list[Any]) -> list[tuple[str, Any]]:
    """Every route of ``routes`` under ``/api/admin`` with its full path, through included
    routers (FastAPI keeps them as nested entries of ``app.routes``)."""
    found: list[tuple[str, Any]] = []
    for context in iter_route_contexts(routes):
        path = context.path or getattr(context.original_route, "path", "") or ""
        if path.startswith("/api/admin"):
            found.append((path, context.original_route))
    return found


def gate_problems(routes: list[Any]) -> list[str]:
    """For the router guard: routes under ``/api/admin`` without ``admin_gate`` (or, for M1's
    ``whoami``, without ``require_admin``), or that are not plain API routes (a mount or a
    websocket would bypass the check). It reads the dependencies of the route that handles the
    request (``original_route``), so a gate added only by ``include_router`` does not count."""
    problems: list[str] = []
    for path, route in admin_area_routes(routes):
        if not isinstance(route, APIRoute):
            problems.append(f"{path}: not an API route ({type(route).__name__})")
            continue
        calls = _calls(route.dependant)
        needed = auth.require_admin if path == "/api/admin/whoami" else admin_gate
        if needed not in calls:
            problems.append(f"{sorted(route.methods or set())} {path}: no {needed.__name__}")
    return problems


def _calls(dependant: Any) -> set[Any]:
    found: set[Any] = set()
    todo = [dependant]
    while todo:
        node = todo.pop()
        if node.call is not None:
            found.add(node.call)
        todo.extend(node.dependencies)
    return found
