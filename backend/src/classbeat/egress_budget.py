"""The per-job request budget (ADR 0009 section 8), moved out of ``classbeat.egress``, which
re-exports every name here and is the only caller of ``_spend``: each urllib request and each
request of an allowlisted httpx transport takes one from the budget in force before it is sent.
Builds no client and sends nothing (``tests/egress_scan.py`` still scans this module)."""

import threading
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar


class RequestBudgetExhausted(Exception):
    """A request to a budgeted host would have gone over its job's budget, so it was **never
    sent** (``CLASSBEAT_SPOTIFY_CALLS_PER_JOB``). No text, no host, no count."""

    def __init__(self) -> None:
        super().__init__("request budget used up")


class RequestBudget:
    """How many requests to ``hosts`` one job may still make: ``limit`` in all, ``used`` of them
    already (across its stages and attempts; the runner reads and stores ``used``). Every
    request and every redirect hop to one of the hosts takes one before it is sent; the one that
    would go over ``limit`` raises ``RequestBudgetExhausted`` instead. Thread-safe: the library's
    calls run in worker threads (``asyncio.to_thread`` carries the context)."""

    def __init__(self, hosts: frozenset[str], limit: int, used: int = 0) -> None:
        if limit < 0 or used < 0:
            raise ValueError("a request budget is not negative")
        self.hosts = hosts
        self.limit = limit
        self._used = used
        self._lock = threading.Lock()

    @property
    def used(self) -> int:
        with self._lock:
            return self._used

    def take(self, host: str) -> None:
        if host.lower() not in self.hosts:
            return
        with self._lock:
            if self._used >= self.limit:
                raise RequestBudgetExhausted
            self._used += 1


_request_budget: ContextVar[RequestBudget | None] = ContextVar("request_budget", default=None)


@contextmanager
def request_budget(budget: RequestBudget) -> Iterator[RequestBudget]:
    """Inside the block (and in threads started from it with the context, as ``asyncio.to_thread``
    does), every urllib and ``spotify_async_client`` request to the budget's hosts is counted."""
    token = _request_budget.set(budget)
    try:
        yield budget
    finally:
        _request_budget.reset(token)


def _spend(host: str | None) -> None:
    budget = _request_budget.get()
    if budget is not None and host is not None:
        budget.take(host)
