"""The worker loop."""

import asyncio
import logging

from classbeat.config import Settings
from classbeat.db import create_engine

logger = logging.getLogger(__name__)


async def run_worker(settings: Settings, stop: asyncio.Event) -> None:
    """Run until ``stop`` is set, logging a heartbeat every ``worker_heartbeat_seconds``."""
    engine = create_engine(settings)  # connects lazily; job processing arrives in M3a
    try:
        logger.info("worker started")
        while not stop.is_set():
            try:
                await asyncio.wait_for(stop.wait(), timeout=settings.worker_heartbeat_seconds)
            except TimeoutError:
                logger.info("worker heartbeat")
        logger.info("worker stopped")
    finally:
        await engine.dispose()
