"""What the Paddle webhook checks before the signature and before any database access (ADR 0032
section 3): an anonymous rate limit, a small body cap, and the per-process semaphore of 1.

All in memory, per process, on purpose: the classbeat rate limits live in Postgres
(`auth.ratelimit`), and a delivery that is not yet verified must not reach the database.

- **Rate limit:** a fixed window per client network (`auth.bucket_ip`: an IPv4 address, an IPv6
  /64), `PER_IP` requests a minute, and `TOTAL` a minute for the process; above either, 429 and
  Paddle retries. At most `TRACKED` networks are remembered: a full table is cleared when a new
  network arrives, and only the table: the process-wide count (`_all`) is its own and runs to the
  end of the window, so many networks cannot reset it.
- **Body cap:** `MAX_BODY` bytes, counted as the body streams in (a lying or missing
  `Content-Length` is not believed); over it, 413. Paddle's notifications are a few KiB.
- **Busy:** one webhook's database work at a time per process (`Busy`), because the maint pool
  has 2 connections shared with admins and testers; a delivery that finds it taken gets 503 and
  Paddle retries. With several web processes each has its own.
"""

from __future__ import annotations

import time
from collections.abc import AsyncIterator
from typing import Final

MAX_BODY: Final = 32 * 1024
PER_IP: Final = 60
TOTAL: Final = 300
WINDOW_S: Final = 60
TRACKED: Final = 4096


class TooLarge(Exception):
    pass


class WebhookLimiter:
    def __init__(self, *, per_ip: int = PER_IP, total: int = TOTAL) -> None:
        self._per_ip, self._total = per_ip, total
        self._window = -1
        self._counts: dict[str, int] = {}
        self._all = 0

    def allow(self, network: str, *, now: float | None = None) -> bool:
        window = int((time.monotonic() if now is None else now) // WINDOW_S)
        if window != self._window:
            self._window, self._counts, self._all = window, {}, 0
        elif network not in self._counts and len(self._counts) >= TRACKED:
            self._counts = {}  # the per-network table only: `_all` keeps counting
        self._all += 1
        count = self._counts.get(network, 0) + 1
        self._counts[network] = count
        return count <= self._per_ip and self._all <= self._total


class Busy:
    """The per-process semaphore of 1 that never waits: `take` is `False` when it is held."""

    def __init__(self) -> None:
        self._held = False

    def take(self) -> bool:
        # No await between the check and the set: one event loop runs one of them at a time.
        if self._held:
            return False
        self._held = True
        return True

    def release(self) -> None:
        self._held = False

    @property
    def held(self) -> bool:
        return self._held


async def read_body(chunks: AsyncIterator[bytes], limit: int = MAX_BODY) -> bytes:
    """The raw body, exactly as sent, or `TooLarge` as soon as it passes `limit`."""
    body = bytearray()
    async for chunk in chunks:
        body += chunk
        if len(body) > limit:
            raise TooLarge
    return bytes(body)


class WebhookGate:
    """The app's one gate (`app.state.paddle_webhook`)."""

    def __init__(self) -> None:
        self.limiter = WebhookLimiter()
        self.busy = Busy()
