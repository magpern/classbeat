"""The SwedeBeat studio's routes in the order the web app mounts them (moved out of ``web.py``,
#1394): its song, design, output and sync stages, then the audition's sessions and clips."""

from fastapi import FastAPI

from classbeat.classes.preview_routes import install as install_preview
from classbeat.classes.studio_design_routes import router as studio_design_router
from classbeat.classes.studio_output_actions_routes import router as studio_output_actions_router
from classbeat.classes.studio_outputs_routes import router as studio_outputs_router
from classbeat.classes.studio_songs_routes import router as studio_songs_router
from classbeat.classes.studio_sync_routes import router as studio_sync_router
from classbeat.config import Settings


def install(app: FastAPI, settings: Settings) -> None:
    app.include_router(studio_songs_router)
    app.include_router(studio_design_router)
    app.include_router(studio_outputs_router)  # SR C6a: timeline and output choice
    app.include_router(studio_output_actions_router)  # SR C7: output states and their actions
    app.include_router(studio_sync_router)  # SR C8: Spotify sync state and Sync again
    install_preview(app, settings)  # ADR 0042 section 6.5: the audition's sessions and clips
