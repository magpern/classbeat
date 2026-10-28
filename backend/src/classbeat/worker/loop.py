"""The worker loop."""

import asyncio
import logging
import time

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from classbeat import auth
from classbeat.config import Settings
from classbeat.db import create_engine, create_session_factory

logger = logging.getLogger(__name__)


async def purge_once(factory: async_sessionmaker[AsyncSession], settings: Settings) -> None:
    """Run the hourly purge of expired auth rows. A failure is logged and retried next hour."""
    try:
        counts = await auth.purge_expired(factory, settings)
    except Exception as exc:  # the worker must outlive a database blip
        logger.warning("auth purge failed", extra={"error_type": type(exc).__name__})
        return
    logger.info("auth purge", extra={"deleted": counts})


async def run_worker(settings: Settings, stop: asyncio.Event) -> None:
    """Run until ``stop`` is set: a heartbeat every ``worker_heartbeat_seconds`` and the auth
    purge every ``purge_interval_seconds``."""
    engine = create_engine(settings)  # connects lazily
    factory = create_session_factory(engine)
    try:
        logger.info("worker started")
        next_purge = time.monotonic() + settings.purge_interval_seconds
        while not stop.is_set():
            timeout = min(settings.worker_heartbeat_seconds, max(next_purge - time.monotonic(), 0))
            try:
                await asyncio.wait_for(stop.wait(), timeout=timeout)
            except TimeoutError:
                if time.monotonic() >= next_purge:
                    await purge_once(factory, settings)
                    next_purge = time.monotonic() + settings.purge_interval_seconds
                else:
                    logger.info("worker heartbeat")
        logger.info("worker stopped")
    finally:
        await engine.dispose()
