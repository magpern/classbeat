"""The rate limit of the read-only mix choices preview (ADR 0036 amendment B, B4, #1369), in the
``design/mixlimit.py`` pattern of a per-user count in a window with a named refusal, but **in this
process's memory**: the preview stores nothing (a row per preview would be a write), and a
preview is a bounded read and a pure computation, so a per-process bound is enough to keep one
user from tying up the web workers. ``rate_limited`` (429) beyond ``PREVIEWS_PER_MINUTE``.
"""

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Final
from uuid import UUID

PREVIEWS_PER_MINUTE: Final = 60
WINDOW_SECONDS: Final = 60


@dataclass(slots=True)
class PreviewLimiter:
    """Fixed windows of ``WINDOW_SECONDS``: at most ``per_user`` previews per user in each."""

    per_user: int = PREVIEWS_PER_MINUTE
    clock: Callable[[], float] = time.monotonic
    _window: int = -1
    _counts: dict[UUID, int] = field(default_factory=lambda: dict[UUID, int]())

    def allow(self, user_id: UUID) -> bool:
        window = int(self.clock() // WINDOW_SECONDS)
        if window != self._window:
            self._window, self._counts = window, {}
        used = self._counts.get(user_id, 0)
        if used >= self.per_user:
            return False
        self._counts[user_id] = used + 1
        return True


LIMITER: Final = PreviewLimiter()
