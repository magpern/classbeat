"""The pace of polling spotdown (ADR 0040 section 8). Pure: no I/O.

The first poll comes after ``POLL_FIRST_SECONDS``, then the wait doubles up to
``POLL_MAX_SECONDS``; a ``rate_limited`` answer waits its (clamped) ``retry_after_s``; never past
the budget the caller has left.
"""

from typing import Final

POLL_FIRST_SECONDS: Final = 5.0
POLL_MAX_SECONDS: Final = 30.0


def poll_delay(polls: int, retry_after_s: float | None, left: float) -> float:
    """The wait before the next round of polls (``polls`` rounds so far), within ``left``."""
    delay = min(POLL_FIRST_SECONDS * 2 ** max(polls, 0), POLL_MAX_SECONDS)
    if retry_after_s is not None:
        delay = max(delay, retry_after_s)
    return max(min(delay, left), 0.0)
