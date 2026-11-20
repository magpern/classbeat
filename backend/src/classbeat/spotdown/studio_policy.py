"""The failure reactions and the wait budget of studio tracks analysed by spotdown (ADR 0040
section 8, plan section 6; SD-P5, #1302). Pure: no I/O.

spotdown is never a hard dependency: every outcome but a map ends in the **local analysis of that
track** (``fallback``), and only that track changes. Per failure (``reaction``):

- spotdown **unreachable** (any reason: no answer, a 5xx, a refused key, a bad answer): this track
  falls back and the rest of the job is analysed locally without calling spotdown (``outage``);
- ``rate_limited``: wait its (clamped) ``retry_after_s`` and try again, not counted as an attempt,
  within the budget (``wait``);
- ``provider_error``: one retry after ``RETRY_PAUSE_SECONDS`` (``retry``), then the fallback. When
  at least ``OUTAGE_MIN_TRACKS`` tracks and half of those tried in the run end so, it is a
  provider outage (``provider_outage``): the rest of the job is local;
- ``processing_failed``: deterministic, so no retry on the same audio, except one after a
  ``timeout``; then the fallback;
- ``alignment_failed`` and every other status (none applies to an upload): the fallback.

**The budget** (``remaining``): a design job waits for spotdown (its polls, its ``rate_limited``
waits) at most ``WAIT_SECONDS`` (the "about 10 min" of section 8) in all, counted from the job's
first spotdown call, **over every run of the job** (``design.track_spotdown_budget``); then every
track left is analysed locally. An outage found by one run holds for the whole job too.
``rate_limited`` waits in the run (with checkpoints, within this budget) instead of a fenced
requeue (ADR 0040 section 8, amendment of 2026-11-20).
"""

from datetime import datetime, timedelta
from typing import Final, Literal

from classbeat.spotdown.failure_policy import TrackFailure, Unreachable

WAIT_SECONDS: Final = 600.0
RETRY_PAUSE_SECONDS: Final = 5.0
OUTAGE_MIN_TRACKS: Final = 3

Reaction = Literal["fallback", "retry", "wait", "outage"]


def reaction(failure: TrackFailure | Unreachable, retried: bool) -> Reaction:
    """What to do after ``failure``; ``retried``: this track's one counted retry is spent."""
    if isinstance(failure, Unreachable):
        return "outage"
    if failure.status == "rate_limited":
        return "wait"
    retryable = failure.status == "provider_error" or (
        failure.status == "processing_failed" and failure.failure_kind == "timeout"
    )
    return "retry" if retryable and not retried else "fallback"


def provider_outage(provider_errors: int, tried: int) -> bool:
    """Whether the run's ``provider_error`` tracks make an outage (section 8's rule)."""
    return provider_errors >= OUTAGE_MIN_TRACKS and 2 * provider_errors >= tried


def wait_for(retry_after_s: float | None, left: float) -> float | None:
    """The wait before trying again after ``rate_limited``; ``None`` when it does not fit."""
    wait = max(retry_after_s or 0.0, 0.0)
    return wait if wait < left else None


def remaining(started: datetime, now: datetime) -> float:
    """Seconds left of the job's budget that began at ``started``."""
    spent = (now - started) / timedelta(seconds=1)
    return min(max(WAIT_SECONDS - spent, 0.0), WAIT_SECONDS)
