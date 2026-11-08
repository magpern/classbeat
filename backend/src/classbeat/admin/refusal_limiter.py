"""The refusal-row limiter of the admin area (ADR 0011 section 6), moved out of
``classbeat.admin.audit`` (which re-exports it): refusal rows per minute, per admin and overall,
so neither ``admin_audit`` nor the maint pool can be flooded by refusals. Pure: a clock, no I/O
but a log line."""

import logging
import time
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class RefusalLimiter:
    """Refusal rows per minute (ADR 0011 section 6): at most ``per_admin`` per admin and
    ``total`` overall, in fixed one-minute windows of this process; beyond that a log count.
    Neither the table nor the maint pool (2 connections) can be exhausted by refusals."""

    per_admin: int = 20
    total: int = 100
    clock: Any = time.monotonic
    _window: float = -1.0
    _counts: dict[UUID, int] = field(default_factory=lambda: dict[UUID, int]())
    _total: int = 0
    _dropped: int = 0

    def allow(self, admin_id: UUID) -> bool:
        window = float(int(self.clock() // 60))
        if window != self._window:
            if self._dropped:
                logger.warning("admin refusal rows not written", extra={"dropped": self._dropped})
            self._window, self._counts, self._total, self._dropped = window, {}, 0, 0
        if self._total >= self.total or self._counts.get(admin_id, 0) >= self.per_admin:
            self._dropped += 1
            return False
        self._counts[admin_id] = self._counts.get(admin_id, 0) + 1
        self._total += 1
        return True
