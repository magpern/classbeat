"""The worker's maintenance steps around the job loop (``classbeat.worker.loop``): the hourly
auth purge and the wrapper that keeps every maintenance step from stopping the worker. A failure is
logged by type and retried at the next round; ``DatabaseRoleError`` stops the worker."""

import logging
from collections.abc import Awaitable, Callable

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from classbeat import auth
from classbeat.config import Settings
from classbeat.db import DatabaseRoleError

logger = logging.getLogger("classbeat.worker.loop")

Factory = async_sessionmaker[AsyncSession]


async def purge_once(factory: Factory, settings: Settings) -> None:
    """Run the hourly purge of expired auth rows. A failure is logged and retried next hour;
    ``DatabaseRoleError`` stops the worker."""
    try:
        counts = await auth.purge_expired(factory, settings)
    except DatabaseRoleError:
        raise
    except Exception as exc:  # the worker must outlive a database blip
        logger.warning("auth purge failed", extra={"error_type": type(exc).__name__})
        return
    logger.info("auth purge", extra={"deleted": counts})


async def quietly(name: str, step: Callable[[], Awaitable[int]]) -> None:
    try:
        count = await step()
    except DatabaseRoleError:
        raise
    except Exception as exc:
        logger.warning(
            "job maintenance failed", extra={"step": name, "error_type": type(exc).__name__}
        )
        return
    if count:
        logger.info("job maintenance", extra={"step": name, "count": count})
