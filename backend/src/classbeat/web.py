"""The web app: JSON API under ``/api`` and, optionally, the built single-page app."""

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy import text

from classbeat import __version__, auth
from classbeat.config import Settings, check_web_has_no_model_key
from classbeat.connections.routes import router as connections_router
from classbeat.crypto.keks import load_keyring
from classbeat.db import create_engine, create_session_factory, verify_database_role
from classbeat.jobs.routes import router as jobs_router

logger = logging.getLogger(__name__)

READY_TIMEOUT_SECONDS = 5.0


class HealthResponse(BaseModel):
    status: Literal["ok"]
    version: str


class ReadyResponse(BaseModel):
    status: Literal["ready", "unavailable"]


api = APIRouter(prefix="/api")


@api.get("/health")
async def health() -> HealthResponse:
    """Liveness: the process is up. Never touches the database."""
    return HealthResponse(status="ok", version=__version__)


@api.get("/ready", responses={503: {"model": ReadyResponse}})
async def ready(request: Request, response: Response) -> ReadyResponse:
    """Readiness: the database answers ``SELECT 1``. No error detail in the body."""
    try:
        async with asyncio.timeout(READY_TIMEOUT_SECONDS):
            async with request.app.state.session_factory() as session:
                await session.execute(text("SELECT 1"))
    except Exception as exc:  # any failure means "not ready"
        # The exception text can name hosts and users, so only its type is logged.
        logger.warning("database not ready", extra={"error_type": type(exc).__name__})
        response.status_code = 503
        return ReadyResponse(status="unavailable")
    return ReadyResponse(status="ready")


def _add_single_page_app(app: FastAPI, static_dir: Path) -> None:
    """Serve ``static_dir``; any other GET outside ``/api`` gets ``index.html``."""
    root = static_dir.resolve()

    @app.get("/{path:path}", include_in_schema=False)
    async def single_page_app(path: str) -> FileResponse:
        if path == "api" or path.startswith("api/"):
            raise HTTPException(status_code=404)
        try:
            candidate = (root / path).resolve()
            if candidate.is_relative_to(root) and candidate.is_file():
                return FileResponse(candidate)
        except (OSError, ValueError):  # for example an embedded NUL in the path
            pass
        index = root / "index.html"
        if not index.is_file():
            raise HTTPException(status_code=404)
        return FileResponse(index)


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build the app. The database engine is created in the lifespan, not here."""
    settings = settings if settings is not None else Settings()
    check_web_has_no_model_key(settings)  # the model key is the worker's only (ADR 0006 §9)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        engine = create_engine(settings)
        try:
            await verify_database_role(engine)  # refuses to start on a privileged role
            app.state.engine = engine
            app.state.session_factory = create_session_factory(engine)
            # Refuses to start without a valid connection_kek, or with a key swapped under an
            # unchanged id (ADR 0004 section 2).
            app.state.keyring = await load_keyring(
                app.state.session_factory, settings.connection_kek_id
            )
            auth.attach_state(app, settings, app.state.session_factory)
            yield
        finally:
            auth.detach_state(app)
            await engine.dispose()

    docs_enabled = settings.environment != "prod"
    app = FastAPI(
        title="classbeat",
        version=__version__,
        lifespan=lifespan,
        docs_url="/api/docs" if docs_enabled else None,
        redoc_url=None,
        openapi_url="/api/openapi.json" if docs_enabled else None,
    )
    app.state.settings = settings
    app.include_router(api)
    auth.install(app)
    app.include_router(connections_router)
    app.include_router(jobs_router)

    if settings.static_dir is not None:
        if settings.static_dir.is_dir():
            _add_single_page_app(app, settings.static_dir)
        else:
            logger.warning("static_dir does not exist; not serving static files")
    return app
