"""The rules of shadow mode for studio tracks (ADR 0040 sections 8 and 9, rollout step 1). Pure: no
I/O.

- **The wait budget** (``remaining``): a design job waits for its shadow analyses at most
  ``WAIT_SECONDS`` (the "about 10 min" spotdown wait budget of section 8) in all, counted from the
  job's first shadow relay, over every run of the job; then what is still pending is
  ``timed_out``.
- **The pace** (``poll_delay``): the first poll after ``POLL_FIRST_SECONDS``, then doubling to
  ``POLL_MAX_SECONDS``; a ``rate_limited`` answer waits its (clamped) ``retry_after_s``; never past
  the budget.
- **The outcome** of each track is one of ``OUTCOMES`` with an optional ``detail`` code (a failure
  status, an unreachable reason, why the spotdown map would be refused); ``failure_outcome`` maps
  a typed failure of ``failure_policy``. Nothing here keeps text from an answer: a detail is a
  code of ``failure_policy``'s literals or this module's, and an unknown one is ``other``.
"""

import re
from datetime import datetime, timedelta
from typing import Final, Literal

from classbeat.spotdown.failure_policy import TrackFailure, Unreachable

WAIT_SECONDS: Final = 600.0
POLL_FIRST_SECONDS: Final = 5.0
POLL_MAX_SECONDS: Final = 30.0
DETAIL: Final = re.compile(r"[a-z_]{1,40}")

Outcome = Literal[
    "pending",  # relayed; the run ended before the comparison did (a cancel, shutdown, lost lease)
    "compared",  # spotdown's analysis was read and compared
    "track_failure",  # a per-track status of section 8 (detail: the status)
    "unreachable",  # spotdown unreachable for a call (detail: the reason)
    "timed_out",  # the job's wait budget ran out first
    "bad_document",  # an analysis without a readable provenance
    "internal",  # an unexpected error in the shadow step, swallowed
]
OUTCOMES: Final[frozenset[str]] = frozenset(
    {
        "pending",
        "compared",
        "track_failure",
        "unreachable",
        "timed_out",
        "bad_document",
        "internal",
    }
)


def remaining(first_started: datetime | None, now: datetime) -> float:
    """Seconds left of the job's budget (the whole budget before its first relay)."""
    if first_started is None:
        return WAIT_SECONDS
    spent = (now - first_started) / timedelta(seconds=1)
    return min(max(WAIT_SECONDS - spent, 0.0), WAIT_SECONDS)


def poll_delay(polls: int, retry_after_s: float | None, left: float) -> float:
    """The wait before the next round of polls (``polls`` rounds so far), within ``left``."""
    delay = min(POLL_FIRST_SECONDS * 2 ** max(polls, 0), POLL_MAX_SECONDS)
    if retry_after_s is not None:
        delay = max(delay, retry_after_s)
    return max(min(delay, left), 0.0)


def detail_code(value: str | None) -> str | None:
    """A detail as stored: a code, else ``other``; ``None`` stays ``None``."""
    if value is None:
        return None
    return value if DETAIL.fullmatch(value) else "other"


def failure_outcome(failure: TrackFailure | Unreachable) -> tuple[Outcome, str | None]:
    if isinstance(failure, Unreachable):
        return "unreachable", detail_code(failure.reason)
    return "track_failure", detail_code(failure.status)
