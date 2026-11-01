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
from classbeat.egress import OutboundBlocked, RequestBudgetExhausted

# ADR 0005 section 4, ADR 0007 section 1, ADR 0008 section 1 and ADR 0009 section 8; the same list
# as the ``jobs.error_category`` check of migration 0006. Which of the new ones are retried is
# decided with the code that raises them (``RETRYABLE`` is unchanged until then).
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
    "program_changed",
    "program_missing",
    "music_unlinked",
    # ADR 0009 section 8 (migration 0006).
    "invalid_grant",
    "app_misconfigured",
    "not_allowlisted",
    "forbidden",
    "quota_exhausted",
    "not_found",
    "scope_mismatch",
    "invalid_response",
    "account_changed",
    "access_denied",
    # ADR 0008 section 1 (migration 0006).
    "consent_missing",
    "pool_unusable",
    "pool_missing",
    "pool_not_synced",
    "playlist_mismatch",
    "pool_too_small",
    # ADR 0008 section 6 (migration 0011): a revise request that only asks for other songs.
    "use_change_songs",
    # ADR 0016 sections 3 and 4 (migration 0019): the Swedebeat mix fetch and analysis; none is
    # retried (a retry would fetch the same refused link or the same hostile file again).
    "media_host_not_allowed",
    "media_refused",
    "analysis_failed",
    # ADR 0016 sections 2 and 7 (migration 0020): a design on a Swedebeat mix whose run finds
    # ``swedebeat_enabled`` off, and the daily limit of new analyses; neither is retried.
    "feature_off",
    "analysis_limit",
)
# Retried by the worker (a new attempt with backoff). ``lease_lost`` is retried by the reaper and
# ``operation_busy`` is requeued without an attempt (``classbeat.jobs.transitions``).
# ``pool_missing`` (ADR 0008 sections 1 and 2, OP1): the pool playlist was gone; its id and the
# pool operation rows are forgotten, so the retry creates a new pool under a new key and ``ref``.
RETRYABLE: Final = frozenset(
    {"upstream_unavailable", "rate_limited", "model_unavailable", "pool_missing"}
)
# Retried once per phase, then failed (ADR 0008 section 1: ``pool_not_synced``, "once more":
# MOWL had not caught up with the pool playlist; a second miss is the user's to retry later).
RETRIED_ONCE: Final = frozenset({"pool_not_synced"})

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
    # ADR 0009 section 8's Spotify categories that are job categories too (migration 0006), so
    # a Spotify write's refusal keeps its category instead of ``internal`` (OP1). None of them is
    # retried; ``rate_limited`` and ``upstream_unavailable`` are above.
    "invalid_grant": "invalid_grant",
    "app_misconfigured": "app_misconfigured",
    "forbidden": "forbidden",
    "quota_exhausted": "quota_exhausted",
    "not_found": "not_found",
    "invalid_response": "invalid_response",
}


# ADR 0009 section 8: this many attempts in a row ending with a Spotify 429 are
# ``quota_exhausted`` (a MOWL rate limit does not count).
QUOTA_RATE_LIMITS: Final = 3
MAX_HINT_SECONDS: Final = 86_400  # a longer Retry-After is kept as a day

# A provider's ``Retry-After`` (seconds) is kept for these (ADR 0009 section 8's sentences).
HINTED: Final = frozenset({"rate_limited", "quota_exhausted"})


class JobFailure(Exception):
    """A run failed with ``category``. ``args == (category,)``; ``retry_after`` (seconds) only
    for ``rate_limited`` and ``quota_exhausted``; ``provider`` is ``"spotify"`` for a failure
    of a Spotify request (``ConnectionFailure.provider``), else None."""

    def __init__(
        self, category: str, retry_after: float | None = None, *, provider: str | None = None
    ) -> None:
        if category not in CATEGORIES:
            category = "internal"
        super().__init__(category)
        self.category = category
        self.retry_after = retry_after if category in HINTED else None
        self.provider = provider


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
        return JobFailure(category, exc.retry_after, provider=exc.provider)
    if isinstance(exc, RequestBudgetExhausted):  # ADR 0009 section 8: the per-job call cap
        return JobFailure("limit_exceeded")
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
