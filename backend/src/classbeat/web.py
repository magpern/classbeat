"""The web app: JSON API under ``/api`` and, optionally, the built single-page app."""

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Literal

from fastapi import APIRouter, FastAPI, Request, Response
from pydantic import BaseModel
from sqlalchemy import text

# saving registers save_workout (SV1); playlists the Spotify writes for Check again (OP1)
from classbeat import (
    __version__,
    auth,
    playlists,
    quota,
    saving,
    songs,
    swedebeat_handoff,
    swedebeat_match,
)
from classbeat.account import erase
from classbeat.account.routes import router as account_router
from classbeat.admin import routes as admin_routes
from classbeat.admin.billing import router as admin_billing_router
from classbeat.admin.db import AdminDb
from classbeat.admin.invite_requests import router as admin_invite_requests_router
from classbeat.billing.errors import BillingRefused
from classbeat.billing.routes import refusal_response as billing_refusal
from classbeat.billing.routes import router as billing_router
from classbeat.bodylimit import BodyLimit
from classbeat.classes.duplicate import router as duplicate_router
from classbeat.classes.handoff_routes import install_job_enqueue
from classbeat.classes.handoff_routes import router as handoff_router
from classbeat.classes.history import router as history_router
from classbeat.classes.interpretation_routes import router as interpretation_router
from classbeat.classes.routes import music_router as class_music_router
from classbeat.classes.routes import router as classes_router
from classbeat.config import Settings, check_web_has_no_model_key
from classbeat.connections.mowl_qr import QrWaits
from classbeat.connections.mowl_qr_routes import router as mowl_qr_router
from classbeat.connections.routes import router as connections_router
from classbeat.connections.swedebeat_routes import router as swedebeat_router
from classbeat.crypto.keks import load_keyring
from classbeat.db import create_engine, create_session_factory, verify_database_role
from classbeat.design import configure_limits, mixadmit, music_kind, sport
from classbeat.design.mixes import router as mixes_router
from classbeat.design.routes import router as music_router  # registers design_draft (J3)
from classbeat.emails.handoff import HandoffConsumer
from classbeat.flags.routes import router as settings_router
from classbeat.headers import SecurityHeaders, unhandled_error_handler
from classbeat.jobs.events_routes import StreamLimiter
from classbeat.jobs.events_routes import router as job_events_router
from classbeat.jobs.hub import EventHub
from classbeat.jobs.routes import enqueue as enqueue_job
from classbeat.jobs.routes import router as jobs_router
from classbeat.operations.routes import router as operations_router
from classbeat.plans.catalogue_routes import router as catalogue_router
from classbeat.plans.routes import router as plans_router
from classbeat.playlists.runtime import playlists_runtime
from classbeat.quota import PlanRevisionPolicy
from classbeat.spa_routes import add_single_page_app
from classbeat.spotify.routes import api as spotify_router
from classbeat.spotify.routes import callback as spotify_callback_router
from classbeat.usage.routes import router as usage_router

# The hand-off routes create jobs through the jobs API. The session guard allows
# classbeat.jobs.service only from classbeat.jobs.routes, and that module only from here.
install_job_enqueue(enqueue_job)

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


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build the app. The database engine is created in the lifespan, not here."""
    settings = settings if settings is not None else Settings()
    check_web_has_no_model_key(settings)  # the model key is the worker's only (ADR 0006 §9)
    # The save reconciler of the user's Check again (ADR 0007 section 8).
    saving.configure_listing_lag(settings.mowl_listing_lag_seconds)
    # The Spotify create's and replace's reconcilers of the user's Check again (OP1).
    playlists.configure(playlists_runtime(settings))

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        engine = create_engine(settings)
        try:
            await verify_database_role(engine)  # refuses to start on a privileged role
            app.state.engine = engine
            app.state.session_factory = create_session_factory(engine)
            # Refuses to start without a valid connection_kek, or with a key swapped under an
            # unchanged id (ADR 0004 section 2).
            # The operator's delete of an account ends the target's jobs through this value
            # (ADR 0014 section 3); the admin package does not import the jobs code (guard 11).
            app.state.end_account_jobs = erase.make_job_ender(
                app.state.session_factory,
                quota.MeteredLedger(quota.Bounds.from_settings(settings)),
            )
            app.state.keyring = await load_keyring(
                app.state.session_factory, settings.connection_kek_id
            )
            # design_draft's admit on a Swedebeat mix: the switch and the user's own listing
            # (ADR 0016 section 3).
            mixadmit.configure(
                mixadmit.MixAdmission(
                    enabled=settings.swedebeat_enabled,
                    listing=mixadmit.web_listing(app.state.session_factory, app.state.keyring),
                )
            )
            auth.attach_state(app, settings, app.state.session_factory)
            # The admin area's audited role (ADR 0011 section 5): refuses to start on a wrong
            # role; without its credentials the admin routes answer 503 admin_unavailable.
            app.state.admin_db = AdminDb.from_settings(settings)
            if app.state.admin_db is not None:
                await app.state.admin_db.verify()
            app.state.stream_limiter = StreamLimiter()
            app.state.event_hub = EventHub(engine)
            app.state.event_hub.start()  # LISTEN job_events (ADR 0005 section 6)
            # The worker's "class ready" hand-off (ADR 0022 section 4, ME5): LISTEN mail_handoff
            # and a poll when SMTP is configured, else an hourly purge of rows past a day.
            app.state.mail_handoff = HandoffConsumer(
                engine, app.state.session_factory, app.state.mailer, settings
            )
            app.state.mail_handoff.start()
            yield
        finally:
            mixadmit.configure(None)
            consumer = getattr(app.state, "mail_handoff", None)
            if consumer is not None:
                await consumer.stop()
            await app.state.mowl_qr.close()  # every MOWL QR wait ends with the process (ADR 0024)
            hub = getattr(app.state, "event_hub", None)
            if hub is not None:
                await hub.stop()
            auth.detach_state(app)
            admin_db = getattr(app.state, "admin_db", None)
            if admin_db is not None:
                await admin_db.dispose()
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
    # The MOWL QR waits of this process (ADR 0024 section 4): in memory, one per user.
    app.state.mowl_qr = QrWaits()
    # The revise kinds' admit reads them (ADR 0007 section 4): the plan's revisions per class
    # under the setting, and the money bounds of the cost cap (ADR 0011 sections 3 and 4).
    configure_limits(settings, PlanRevisionPolicy(settings.revisions_per_class))
    quota.configure(settings)
    # design_with_songs' admit reads the feature switch (ADR 0008 section 10); importing
    # classbeat.songs registered the kind.
    songs.configure(songs.SongConfig.from_settings(settings))
    # The design kinds' admit reads the running gate, with the web's own ``fake`` counting as
    # covering only outside prod (ADR 0018 section 5 and its amendment of 2026-11-04, #509).
    sport.configure(sport.SportGate.from_settings(settings))
    # ... and the soundless switch, with the web's own ``fake`` counting as covering only outside
    # prod (ADR 0007 amendment of 2026-11-03, #527).
    music_kind.configure(music_kind.SoundlessGate.from_settings(settings))
    # swedebeat_match's admit reads the Swedebeat switch (ADR 0016 section 2); importing
    # classbeat.swedebeat_match registered the kind. The web never opens the token for it.
    swedebeat_match.configure(swedebeat_match.MatchConfig.from_settings(settings))
    # swedebeat_verify_mix (B2): the same switch. The web never opens MOWL for the job itself.
    swedebeat_handoff.configure(swedebeat_handoff.HandoffConfig.from_settings(settings))
    app.include_router(api)
    # The Spotify callback (outside /api, ADR 0009 section 2) before the sign-in providers'
    # ``/oauth/{provider}/callback``, which would otherwise answer it with a 404.
    app.include_router(spotify_callback_router)
    auth.install(app)
    admin_routes.install(app)  # every /api/admin route of the admin area (ADR 0011 section 6)
    app.include_router(spotify_router)
    app.include_router(connections_router)
    app.include_router(mowl_qr_router)
    app.include_router(swedebeat_router)
    app.include_router(job_events_router)
    app.include_router(jobs_router)
    app.include_router(usage_router)
    app.include_router(plans_router)
    app.include_router(billing_router)
    app.include_router(admin_billing_router)  # /api/admin/billing (M7a #948)
    app.include_router(admin_invite_requests_router)  # /api/admin/invite-requests (ADR 0033)
    app.add_exception_handler(BillingRefused, billing_refusal)
    app.include_router(catalogue_router)
    app.include_router(account_router)
    app.include_router(settings_router)
    app.include_router(operations_router)
    app.include_router(music_router)
    app.include_router(mixes_router)
    app.include_router(class_music_router)
    app.include_router(classes_router)
    app.include_router(history_router)
    app.include_router(duplicate_router)
    app.include_router(handoff_router)
    app.include_router(interpretation_router)

    if settings.static_dir is not None:
        if settings.static_dir.is_dir():
            add_single_page_app(app, settings.static_dir, settings)
        else:
            logger.warning("static_dir does not exist; not serving static files")
    # Outside the auth middleware, inside SecurityHeaders: a body over the cap is refused before any
    # session, CSRF or database work, and the 413 still carries the security headers (#351).
    app.add_middleware(BodyLimit)
    # Added last, so it is the outermost middleware: the CSP and Referrer-Policy go on every
    # response, the auth middleware's own answers included (ADR 0013 section 9).
    # ADR 0025 section 10: Cloudflare's origin in script-src and frame-src only with real Turnstile.
    turnstile = settings.turnstile_enabled and not settings.turnstile_fake
    app.add_middleware(SecurityHeaders, base_url=settings.public_base_url, turnstile=turnstile)
    # Starlette answers an unhandled exception outside that middleware: its 500 adds the headers.
    app.add_exception_handler(
        Exception, unhandled_error_handler(settings.public_base_url, turnstile=turnstile)
    )
    return app
