"""Wiring: what ``create_app`` adds for accounts and sessions."""

import logging

from fastapi import FastAPI, Request, Response
from fastapi.encoders import jsonable_encoder
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from classbeat.auth.email_routes import router as email_router
from classbeat.auth.keys import Keys
from classbeat.auth.middleware import AuthMiddleware
from classbeat.auth.passwords import PasswordHashBusy, PasswordHasher
from classbeat.auth.ratelimit import RateLimited, RateLimiter
from classbeat.auth.reset_routes import router as reset_router
from classbeat.auth.routes import router
from classbeat.auth.signin_routes import router as signin_router
from classbeat.auth.signup_routes import router as signup_router
from classbeat.config import Settings
from classbeat.log import describe_database_error
from classbeat.mail import Mailer

logger = logging.getLogger(__name__)


def install(app: FastAPI) -> None:
    """Routes, the Origin/CSRF middleware and the 429 and 503 handlers."""
    app.include_router(router)
    for flow in (signup_router, signin_router, reset_router, email_router):
        app.include_router(flow)
    app.add_middleware(AuthMiddleware)

    @app.exception_handler(RateLimited)
    async def rate_limited(_: Request, exc: Exception) -> JSONResponse:
        retry_after = exc.retry_after if isinstance(exc, RateLimited) else 60
        return JSONResponse(
            {"detail": {"reason": "rate_limited"}},
            status_code=429,
            headers={"Retry-After": str(retry_after)},
        )

    @app.exception_handler(DBAPIError)
    async def database_error(_: Request, exc: Exception) -> JSONResponse:
        # Class and SQLSTATE only: the message carries row values (emails, hashes).
        text = describe_database_error(exc) if isinstance(exc, DBAPIError) else "database error"
        logger.error("database error: %s", text)
        return JSONResponse({"detail": "Internal Server Error"}, status_code=500)

    @app.exception_handler(RequestValidationError)
    async def validation_failed(request: Request, exc: Exception) -> Response:
        if not isinstance(exc, RequestValidationError):
            raise exc
        if not request.url.path.startswith("/api/auth/"):
            return await request_validation_exception_handler(request, exc)
        # FastAPI's default echoes ``input`` (a password or token); drop it on auth routes.
        errors = [
            {key: value for key, value in error.items() if key not in ("input", "ctx", "url")}
            for error in exc.errors()
        ]
        return JSONResponse({"detail": jsonable_encoder(errors)}, status_code=422)

    @app.exception_handler(PasswordHashBusy)
    async def hashing_busy(_: Request, __: Exception) -> JSONResponse:
        return JSONResponse(
            {"detail": {"reason": "busy"}}, status_code=503, headers={"Retry-After": "5"}
        )


def attach_state(
    app: FastAPI, settings: Settings, factory: async_sessionmaker[AsyncSession]
) -> None:
    """Derive the keys and build the limiter and the hashing pool. Refuses to start without
    the ``app_secret_key`` secret file."""
    keys = Keys.from_settings(settings)
    app.state.keys = keys
    app.state.limiter = RateLimiter(factory, keys)
    app.state.password_hasher = PasswordHasher(settings.password_hash_workers)
    app.state.mailer = Mailer(settings)


def detach_state(app: FastAPI) -> None:
    hasher: PasswordHasher | None = getattr(app.state, "password_hasher", None)
    if hasher is not None:
        hasher.close()
