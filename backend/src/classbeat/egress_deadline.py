"""The total deadline of one urllib request (ADR 0007 section 6), moved out of
``classbeat.egress``, which re-exports every name here and whose ``DeadlineHTTPSConnection``
arms ``_Watchdog``: the thread's ``call_deadline``, ``request_deadline`` and the watchdog that
shuts a request's socket down at its deadline. Also ``DeadlineStream``, the httpx answer body
with a total deadline (``classbeat.egress.AllowlistTransport`` and ``classbeat.egress_gateway``).
Builds no client and sends nothing (``tests/egress_scan.py`` still scans this module)."""

import asyncio
import contextlib
import math
import socket
import threading
import time
from collections.abc import AsyncIterator, Callable, Iterator
from contextlib import contextmanager
from typing import Final

import httpx

# A request whose caller gave no timeout (urllib's default) still ends within this.
REQUEST_FALLBACK_SECONDS: Final = 60.0
_thread_deadline = threading.local()


class RequestDeadline(TimeoutError):
    """A request outlived its total deadline (or would have started after it). No text."""

    def __init__(self) -> None:
        super().__init__("request deadline passed")


@contextmanager
def call_deadline(seconds: float) -> Iterator[None]:
    """Inside the block, every urllib request this thread makes ends by ``now + seconds``: one
    still running then is cut off, and a later one is refused before it is sent. Nested blocks
    keep the earlier deadline."""
    if not seconds > 0:
        raise ValueError("a call deadline is positive")
    with call_deadline_until(time.monotonic() + seconds):
        yield


@contextmanager
def call_deadline_until(at: float) -> Iterator[None]:
    """``call_deadline`` for an absolute ``time.monotonic()`` instant taken by the caller
    **before** the call was scheduled (a multi-request write takes it before its ``pending``
    row is written). A thread that starts late uses up the time instead of extending it: once
    ``at`` has passed every request is refused before it is sent."""
    if not isinstance(at, int | float) or isinstance(at, bool):
        raise TypeError("a call deadline is a monotonic instant")
    if not math.isfinite(at):  # nan would compare false everywhere and never expire
        raise ValueError("a call deadline is a finite instant")
    previous: float | None = getattr(_thread_deadline, "at", None)
    _thread_deadline.at = float(at) if previous is None else min(previous, float(at))
    try:
        yield
    finally:
        _thread_deadline.at = previous


def call_deadline_at() -> float | None:
    """The ``time.monotonic()`` instant of this thread's innermost ``call_deadline`` (the earliest
    one in force), or None outside every block. Read-only: it builds nothing and sends nothing."""
    at: float | None = getattr(_thread_deadline, "at", None)
    return at


def request_deadline(timeout: object) -> float:
    """The monotonic time by which a request with this ``timeout`` must be over."""
    total = REQUEST_FALLBACK_SECONDS
    if isinstance(timeout, int | float) and not isinstance(timeout, bool) and timeout > 0:
        total = float(timeout)
    at = time.monotonic() + total
    call = call_deadline_at()
    return at if call is None else min(at, call)


class _Watchdog:
    """Shuts the request's socket down at its deadline. It holds a duplicate of the socket
    (``shutdown`` acts on the connection, whatever descriptor it is called on, so it also cuts
    a TLS socket wrapped around the original), closed when the request is over."""

    def __init__(self, at: float) -> None:
        self.at = at
        self.expired = False
        self._lock = threading.Lock()
        self._sock: socket.socket | None = None
        self._timer: threading.Timer | None = None
        self._done = False

    def check(self) -> None:
        if self.expired or time.monotonic() >= self.at:
            self.expired = True
            raise RequestDeadline

    def arm(self, sock: socket.socket) -> None:
        with self._lock:
            if self._done:
                return
            self._sock = sock.dup()
            timer = threading.Timer(max(0.0, self.at - time.monotonic()), self._fire)
            timer.daemon = True
            self._timer = timer
            timer.start()

    def _fire(self) -> None:
        with self._lock:
            if self._done:
                return
            self.expired = True
            self._release(shutdown=True)

    def disarm(self) -> None:
        with self._lock:
            if self._timer is not None:
                self._timer.cancel()
            self._release(shutdown=False)

    def _release(self, *, shutdown: bool) -> None:  # with the lock held
        self._done = True
        sock, self._sock = self._sock, None
        if sock is None:
            return
        if shutdown:
            with contextlib.suppress(OSError):  # closed already
                sock.shutdown(socket.SHUT_RDWR)
        sock.close()

    def guard[T](self, read: Callable[[], T]) -> T:
        """A read of the response: past the deadline it raises ``RequestDeadline`` (also when
        the cut-off made the read fail or come back short)."""
        try:
            out = read()
        except Exception:
            if self.expired:
                raise RequestDeadline from None
            raise
        self.check()
        return out


class DeadlineStream(httpx.AsyncByteStream):
    """An answer's body that must be read by ``at`` (``time.monotonic()``): each chunk waits at
    most until then, so a body trickling in a byte at a time ends with ``RequestDeadline``."""

    def __init__(self, inner: httpx.AsyncByteStream, at: float) -> None:
        self._inner = inner
        self._at = at

    async def __aiter__(self) -> AsyncIterator[bytes]:
        chunks = self._inner.__aiter__()
        while True:
            remaining = self._at - time.monotonic()
            if remaining <= 0:
                raise RequestDeadline
            try:
                async with asyncio.timeout(remaining):
                    chunk = await anext(chunks)
            except StopAsyncIteration:
                return
            except TimeoutError:
                raise RequestDeadline from None
            yield chunk

    async def aclose(self) -> None:
        await self._inner.aclose()
