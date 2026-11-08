"""``LISTEN jobs_queued`` (ADR 0005 section 3): wakes the claim loop. The connection and its
reconnects are ``classbeat.db.listen``'s; if the listener is down, the worker still polls every
``worker_poll_seconds``, so a lost notification only adds latency."""

import asyncio

from sqlalchemy.ext.asyncio import AsyncEngine

from classbeat.db import listen as _db_listen

CHANNEL = "jobs_queued"


async def listen(engine: AsyncEngine, wake: asyncio.Event, stop: asyncio.Event) -> None:
    """Until ``stop`` is set (or cancelled): set ``wake`` on every ``jobs_queued``."""
    await _db_listen.listen(engine, wake, stop, CHANNEL)
