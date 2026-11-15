"""A match's failed call to a job category (ADR 0035 sections 5 and 6). Pure: no I/O.

The gateway answers a bearer it refuses with a ``401`` (``bearer_refused``), but so far it does that
for a failing SwedeBeat token check too (magpern/sb-gateway-service#5). So ``bearer_refused`` is
never a result by itself: the job confirms it with one ``GET /auth/session`` after a pause, and
``confirmed`` maps that check's answer:

- ``200`` (the check passed): ``upstream_unavailable``, retried no sooner than
  ``CACHED_CHECK_SECONDS`` (330 s). The gateway caches a good token check for 300 s, so a bearer
  that SwedeBeat refused mid-match can still pass the check; the retry waits past the cache.
- ``401`` again (not ``unknown client``): ``needs_reconnect`` (the connection flips).
- ``unknown client`` or ``403``: ``app_misconfigured`` (the connection is untouched).
- ``429``: ``rate_limited`` with its ``Retry-After``.
- ``5xx`` or a timeout: ``upstream_unavailable``.
- anything else (a bad body, another status): ``invalid_response``.

``bearer_refused`` is in no category list of jobs or connections, and ``JOB_CATEGORIES`` is every
category this module or a match call can end with.
"""

from typing import Final

CACHED_CHECK_SECONDS: Final = 330
JOB_CATEGORIES: Final = frozenset(
    {
        "app_misconfigured",
        "needs_reconnect",
        "rate_limited",
        "upstream_unavailable",
        "invalid_response",
    }
)
_KEPT: Final = frozenset({"app_misconfigured", "rate_limited", "upstream_unavailable"})


def confirmed(check: tuple[str, int | None] | None) -> tuple[str, int | None]:
    """The job category and wait after the session check: ``check`` is ``None`` when it passed,
    else its failure's ``(category, retry_after)``."""
    if check is None:
        return "upstream_unavailable", CACHED_CHECK_SECONDS
    category, retry_after = check
    if category == "bearer_refused":
        return "needs_reconnect", None
    if category in _KEPT:
        return category, retry_after if category == "rate_limited" else None
    return "invalid_response", None


def call_failed(category: str, retry_after: int | None) -> tuple[str, int | None]:
    """A match call's own failure other than ``bearer_refused``: kept when it is a job category,
    else ``invalid_response`` (never an unknown name in a job row)."""
    if category == "bearer_refused":
        raise ValueError("a refused bearer is confirmed first")
    if category not in JOB_CATEGORIES:
        return "invalid_response", None
    return category, retry_after if category == "rate_limited" else None
