"""Wiring: what ``create_app`` adds for accounts and sessions."""

import logging

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from classbeat.auth.keys import Keys
from classbeat.auth.middleware import AuthMiddleware
from classbeat.auth.passwords import PasswordHashBusy, PasswordHasher
from classbeat.auth.ratelimit import RateLimited, RateLimiter
from classbeat.auth.routes import router
from classbeat.config import Settings
from classbeat.log import describe_database_error

logger = logging.getLogger(__name__)


def install(app: FastAPI) -> None:
    """Routes, the Origin/CSRF middleware and the 429 and 503 handlers."""
    app.include_router(router)
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


def detach_state(app: FastAPI) -> None:
    hasher: PasswordHasher | None = getattr(app.state, "password_hasher", None)
    if hasher is not None:
        hasher.close()
