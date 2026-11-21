"""The failure reactions and the wait budget of studio tracks and mixes analysed by spotdown (ADR
0040 section 8 and its amendment "spotdown is the only analysis path", item 4; plan section 6;
#1353).
Pure: no I/O.

spotdown is the only analysis path: no outcome but a map falls back to anything. Per failure
(``reaction``), after its bounded reaction the job ends in a category (``outage_policy``):

- ``rate_limited``: wait its (clamped) ``retry_after_s`` and try again, not counted as an attempt,
  within the budget (``wait``); a wait that does not fit the budget ends ``analysis_unavailable``;
- ``provider_error``: one retry after ``RETRY_PAUSE_SECONDS`` (``retry``), then
  ``analysis_unavailable`` (retried with the job's backoff: ``unavailable``). A provider outage
  (about half of a batch, at least three tracks) is then never reached: the first track that ends
  so ends the job;
- ``processing_failed``: deterministic, so no retry on the same audio, except one after a
  ``timeout``; then ``analysis_failed`` (``failed``, not retried);
- ``alignment_failed`` and every other status (none applies to an upload): ``analysis_failed``.

An ``Unreachable`` is not a reaction here: ``outage_policy.unreachable_category`` maps its reason.

**The budget** (``remaining``): a design job waits for spotdown (its polls, its ``rate_limited``
waits) at most ``WAIT_SECONDS`` (the "about 10 min" of section 8) in all, counted from the job's
first spotdown call; the budget is the **attempt's**: it is reset when a run ends in a retryable
category (``jobs.transitions.retry`` deletes the job's ``spotdown_studio_budgets`` row), so a
retried ``analysis_unavailable`` is a real retry. A requeue that counts no attempt, and a
lease-lost requeue, keep what was spent. ``rate_limited`` waits in the run (with checkpoints,
within this budget) instead of a fenced requeue (ADR 0040 section 8, amendment of 2026-11-20).
"""

from datetime import datetime, timedelta
from typing import Final, Literal

from classbeat.spotdown.failure_policy import TrackFailure

WAIT_SECONDS: Final = 600.0
RETRY_PAUSE_SECONDS: Final = 5.0

Reaction = Literal["retry", "wait", "unavailable", "failed"]


def reaction(failure: TrackFailure, retried: bool) -> Reaction:
    """What to do after ``failure``; ``retried``: this track's one counted retry is spent."""
    if failure.status == "rate_limited":
        return "wait"
    retryable = failure.status == "provider_error" or (
        failure.status == "processing_failed" and failure.failure_kind == "timeout"
    )
    if retryable and not retried:
        return "retry"
    return "unavailable" if failure.status == "provider_error" else "failed"


def wait_for(retry_after_s: float | None, left: float) -> float | None:
    """The wait before trying again after ``rate_limited``; ``None`` when it does not fit."""
    wait = max(retry_after_s or 0.0, 0.0)
    return wait if wait < left else None


def remaining(started: datetime, now: datetime) -> float:
    """Seconds left of the job's budget that began at ``started``."""
    spent = (now - started) / timedelta(seconds=1)
    return min(max(WAIT_SECONDS - spent, 0.0), WAIT_SECONDS)
