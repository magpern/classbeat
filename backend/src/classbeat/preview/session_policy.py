"""The listening session's server-side limits (ADR 0042 section 6.3). Pure: no I/O.

The server keeps **authorization and reasonable limits only**; the 15-minute listening limit is the
browser's (a monotonic counter, R3). The server cannot verify how long audio was heard and does not
try: this is a product-use deterrent, not DRM and not access control.

- A session lives ``LIFETIME`` (30 min) from its creation; after ``expires_at`` every clip request
  is ``ended`` (``410 preview_session_ended``).
- ``delivered_ms`` counts **each distinct clip once per session** (its length when it is first
  served in the session), never bytes or requests: a media element re-requests a clip with
  ``Range`` on a seek or a reload, and that costs nothing. A new clip that would take the total past
  ``DELIVERY_CAP_MS`` (25 min of audio) is ``ended`` too.
- Session creation and ``preview_render`` jobs are **rate-limited per owner** (resource
  protection, not a quota): ``SESSIONS_PER_HOUR`` sessions, ``RENDERS_PER_HOUR`` render jobs and
  at most ``OPEN_RENDERS`` queued or running render jobs at once.
- Sessions are purged ``KEEP_SESSIONS`` (a day) after their creation; clips and sources after
  ``IDLE_DAYS`` (14) without use.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Final

LIFETIME: Final = timedelta(minutes=30)
DELIVERY_CAP_MS: Final = 25 * 60 * 1000
SESSIONS_PER_HOUR: Final = 30
RENDERS_PER_HOUR: Final = 60
OPEN_RENDERS: Final = 2
KEEP_SESSIONS: Final = timedelta(days=1)
IDLE_DAYS: Final = 14
# The distinct clips one session can count: 25 min of windows of at least 60 s is 25; the windows
# at a mix end can be shorter, so the bound is generous (and the delivery cap holds either way).
MAX_COUNTED: Final = 200


@dataclass(frozen=True, slots=True)
class Delivery:
    """``allowed``: the clip may be served. ``counts``: it is new to the session, so its length is
    added to ``delivered_ms`` and its key to the counted ones."""

    allowed: bool
    counts: bool = False


ENDED: Final = Delivery(False)


def expired(now: datetime, expires_at: datetime) -> bool:
    return now >= expires_at


def delivery(
    now: datetime,
    expires_at: datetime,
    delivered_ms: int,
    counted: Sequence[str],
    key: str,
    clip_ms: int,
) -> Delivery:
    """Whether the clip ``key`` of ``clip_ms`` may be served in this session (see the module
    docstring). A clip already counted in the session is served again at no cost while the
    session lives; a new one only while the total stays within the cap."""
    if expired(now, expires_at) or clip_ms <= 0:
        return ENDED
    if key in counted:
        return Delivery(True)
    if delivered_ms + clip_ms > DELIVERY_CAP_MS or len(counted) >= MAX_COUNTED:
        return ENDED
    return Delivery(True, counts=True)


def would_pass(delivered_ms: int, counted: Sequence[str], key: str, clip_ms: int) -> bool:
    """Whether serving ``key`` later would take the session past its cap: such a clip is never
    rendered for this session (``410`` at once)."""
    if key in counted:
        return False
    return delivered_ms + clip_ms > DELIVERY_CAP_MS or len(counted) >= MAX_COUNTED
