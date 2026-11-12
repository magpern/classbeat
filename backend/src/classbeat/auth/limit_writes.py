"""The rate limiter's writes to ``rate_limits``, one statement each (#998, #1006).

Each write is its own implicit transaction (``db.system_statement``): Postgres commits it before it
answers, so no bucket row lock outlives the statement, whatever happens to the coroutine after it
(an await, a stalled event loop, a cancelled request). A write that still waits past the role's
``lock_timeout`` (another holder, a slow disk) is ``LimitBusy``: the 503 ``busy`` answer with a
short ``Retry-After``, never a pass. The bucket whose reserve timed out took no slot; the buckets
reserved before it in the same ``begin`` keep theirs, and the request is refused. A release that
times out gives back neither that slot nor any after it, so they stay counted. The cover read's
cooldown write (``RateLimiter.start_cooldown``) calls ``system_statement`` directly: its caller
answers ``{covers: []}`` on any database error.
"""

import logging
from collections.abc import Sequence
from datetime import datetime
from typing import Any

from sqlalchemy import Row, and_, delete, func, update
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.sql.dml import UpdateBase

from classbeat.auth.tables import rate_limits
from classbeat.db import system_statement
from classbeat.log import describe_database_error

LOCK_NOT_AVAILABLE = "55P03"  # SQLSTATE of a lock_timeout

errors = logging.getLogger("classbeat.auth.setup")


class LimitBusy(Exception):
    """A limiter write hit the role's lock timeout (SQLSTATE 55P03): 503 ``busy``."""


def is_lock_timeout(exc: DBAPIError) -> bool:
    return getattr(exc.orig, "sqlstate", None) == LOCK_NOT_AVAILABLE


async def limit_statement(
    factory: async_sessionmaker[AsyncSession], statement: UpdateBase
) -> Sequence[Row[Any]]:
    """``system_statement``, with a lock timeout raised as ``LimitBusy`` (logged by class and
    SQLSTATE only: the message carries row values)."""
    try:
        return await system_statement(factory, statement)
    except DBAPIError as exc:
        if not is_lock_timeout(exc):
            raise
        errors.warning("rate limit busy: %s", describe_database_error(exc))
        raise LimitBusy from None


def give_back(bucket_hash: bytes, window_start: datetime | Any) -> UpdateBase:
    """One slot back in one window of one bucket, never below zero."""
    return (
        update(rate_limits)
        .where(
            and_(
                rate_limits.c.bucket_hash == bucket_hash,
                rate_limits.c.window_start == window_start,
            )
        )
        .values(count=func.greatest(rate_limits.c.count - 1, 0))
    )


def forget(bucket_hash: bytes) -> UpdateBase:
    """Every window of one bucket (a success of a bucket marked ``delete``)."""
    return delete(rate_limits).where(rate_limits.c.bucket_hash == bucket_hash)
