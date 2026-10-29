"""Failure categories, retry classes and backoff of a job (ADR 0005 section 4).

A kind's code fails a run by raising ``JobFailure(category)``; anything else it raises is mapped
here (a ``ConnectionFailure`` by its category, ``OutboundBlocked`` to ``outbound_blocked``, a
transient error of our own database to ``DATABASE_RETRY_CATEGORY``, any other exception to
``internal``). Only the category is ever kept: no exception text reaches a job
row, an event or a log.
"""

import random
from typing import Final

from sqlalchemy.exc import DisconnectionError, OperationalError

from classbeat.connections import ConnectionFailure
from classbeat.egress import OutboundBlocked

# ADR 0005 section 4; the same list as the ``jobs.error_category`` check of migration 0004.
CATEGORIES: Final = (
    "upstream_unavailable",
    "rate_limited",
    "model_unavailable",
    "lease_lost",
    "operation_busy",
    "not_connected",
    "needs_reconnect",
    "key_unavailable",
    "decrypt_failed",
    "invalid_input",
    "account_disabled",
    "model_refused",
    "model_config",
    "invalid_output",
    "limit_exceeded",
    "uncertain_write",
    "outbound_blocked",
    "internal",
)
# Retried by the worker (a new attempt with backoff). ``lease_lost`` is retried by the reaper and
# ``operation_busy`` is requeued without an attempt (``classbeat.jobs.transitions``).
RETRYABLE: Final = frozenset({"upstream_unavailable", "rate_limited", "model_unavailable"})

# A transient error of our own database (connection lost, a statement or lock timeout) during a
# checkpoint, a stage write, a kind's own fenced write or the store of a result. ADR 0005 section 4
# has no category of its own for it; the closest retryable one is used (no migration needed), the
# same everywhere (#109 item 3).
DATABASE_RETRY_CATEGORY: Final = "upstream_unavailable"

BACKOFF_BASE_SECONDS: Final = 30.0
BACKOFF_CAP_SECONDS: Final = 900.0  # 15 minutes
RATE_LIMIT_CAP_SECONDS: Final = 3600.0  # a Retry-After hint is honoured up to an hour
JITTER: Final = 0.2  # +-20 %

# ConnectionFailure categories (ADR 0004 section 5) -> job categories.
_FROM_CONNECTION: Final = {
    "needs_reconnect": "needs_reconnect",
    "invalid_credentials": "needs_reconnect",
    "rate_limited": "rate_limited",
    "upstream_unavailable": "upstream_unavailable",
    "key_unavailable": "key_unavailable",
    "decrypt_failed": "decrypt_failed",
    "not_connected": "not_connected",
}


class JobFailure(Exception):
    """A run failed with ``category``. ``args == (category,)``; ``retry_after`` (seconds) only
    for ``rate_limited``."""

    def __init__(self, category: str, retry_after: float | None = None) -> None:
        if category not in CATEGORIES:
            category = "internal"
        super().__init__(category)
        self.category = category
        self.retry_after = retry_after if category == "rate_limited" else None


class LeaseLost(Exception):
    """The fence matched no row: this worker no longer holds the job (ADR 0005 section 3).
    The transaction was rolled back; the run stops and writes nothing more."""


class RunStopped(Exception):
    """A checkpoint stopped the run: ``reason`` is ``cancelled``, ``lost`` or ``shutdown``."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def failure_of(exc: BaseException) -> JobFailure:
    """The ``JobFailure`` for anything a kind's code raised (never its text)."""
    if isinstance(exc, JobFailure):
        return exc
    if isinstance(exc, ConnectionFailure):
        category = _FROM_CONNECTION.get(exc.category, "internal")
        return JobFailure(category, exc.retry_after)
    if isinstance(exc, OutboundBlocked):
        return JobFailure("outbound_blocked")
    if isinstance(exc, OperationalError | DisconnectionError):
        return JobFailure(DATABASE_RETRY_CATEGORY)
    return JobFailure("internal")


def backoff_seconds(
    attempt: int, retry_after: float | None = None, rng: random.Random | None = None
) -> float:
    """``min(30 s * 2^(attempt-1), 15 min)`` with +-20 % jitter; for a rate limit at least its
    hint, capped at an hour. ``attempt`` is the attempt that just failed (1 for the first)."""
    base = min(BACKOFF_BASE_SECONDS * 2 ** (max(attempt, 1) - 1), BACKOFF_CAP_SECONDS)
    delay = base * (1 + (rng or random).uniform(-JITTER, JITTER))
    if retry_after is not None and retry_after > 0:
        delay = max(delay, min(float(retry_after), RATE_LIMIT_CAP_SECONDS))
    return min(delay, RATE_LIMIT_CAP_SECONDS)
