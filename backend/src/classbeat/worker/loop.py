"""The worker loop (ADR 0005 section 3).

Up to ``worker_concurrency`` runs as asyncio tasks. When a slot is free the loop calls
``queue_claim``, woken by ``LISTEN jobs_queued`` (``classbeat.worker.listen``) or a poll every
``worker_poll_seconds``; it claims nothing until the queue check of a connection has passed
(``queue_verified``). Every ``worker_maintenance_seconds`` it runs the reaper, the expiry sweep
and the credit settle; the hourly auth purge stays.

**Shutdown** (SIGTERM; compose gives 30 s): stop claiming; every run stops at its next
checkpoint (no new model turn or external call) and hands its lease back; runs still busy after
``worker_shutdown_wait_seconds`` (25 s) are cancelled, leaving a call in flight ``pending`` under
the old token, and their leases are handed back too. Each hand-back is one fenced requeue under
the role's 5 s ``lock_timeout``; what is not handed back is reaped when its lease expires.
"""

import asyncio
import contextlib
import logging
import os
import re
import secrets
import socket
import time
from collections.abc import Awaitable, Callable
from typing import Any

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from classbeat import auth
from classbeat.config import Settings
from classbeat.db import (
    DatabaseRoleError,
    create_engine,
    create_session_factory,
    queue_verified,
    verify_database_role,
)
from classbeat.jobs import KINDS, CreditLedger, KindRegistry, UnmeteredLedger, queue, settle
from classbeat.jobs.runner import Execution, RunConfig, execute, hand_back
from classbeat.worker.listen import listen

logger = logging.getLogger(__name__)

CHECK_BACKOFF_MAX_SECONDS = 30.0

Factory = async_sessionmaker[AsyncSession]


def worker_pool(settings: Settings) -> dict[str, Any]:
    """The worker engine's pool: per run one pipeline and one heartbeat connection, plus the
    ``LISTEN`` connection and one for claims, maintenance and the queue check; an overflow of
    one per run covers a run that opens a second transaction while its first is open. With the
    cap of 16 runs that is at most 50 connections."""
    return {
        "pool_size": 2 * settings.worker_concurrency + 2,
        "max_overflow": settings.worker_concurrency,
    }


def worker_id() -> str:
    """``host:pid:random``, within ``queue_claim``'s ``^[A-Za-z0-9._:-]{1,100}$``."""
    host = re.sub(r"[^A-Za-z0-9._-]", "-", socket.gethostname())[:60] or "worker"
    return f"{host}:{os.getpid()}:{secrets.token_hex(4)}"


async def purge_once(factory: Factory, settings: Settings) -> None:
    """Run the hourly purge of expired auth rows. A failure is logged and retried next hour."""
    try:
        counts = await auth.purge_expired(factory, settings)
    except Exception as exc:  # the worker must outlive a database blip
        logger.warning("auth purge failed", extra={"error_type": type(exc).__name__})
        return
    logger.info("auth purge", extra={"deleted": counts})


async def _quietly(name: str, step: Callable[[], Awaitable[int]]) -> None:
    try:
        count = await step()
    except Exception as exc:
        logger.warning(
            "job maintenance failed", extra={"step": name, "error_type": type(exc).__name__}
        )
        return
    if count:
        logger.info("job maintenance", extra={"step": name, "count": count})


class Worker:
    def __init__(
        self,
        settings: Settings,
        engine: AsyncEngine,
        *,
        registry: KindRegistry = KINDS,
        ledger: CreditLedger | None = None,
    ) -> None:
        self.settings = settings
        self.engine = engine
        self.factory = create_session_factory(engine)
        self.registry = registry
        self.ledger: CreditLedger = ledger or UnmeteredLedger()
        self.id = worker_id()
        self.config = RunConfig(
            lease_seconds=settings.job_lease_seconds,
            approval_ttl_hours=settings.approval_ttl_hours,
            busy_requeue_limit=settings.job_busy_requeue_limit,
        )
        self.shutdown = asyncio.Event()  # set on SIGTERM: runs stop at their next checkpoint
        self.wake = asyncio.Event()
        self.running: dict[asyncio.Task[None], Execution] = {}
        self.stop = asyncio.Event()  # the caller's stop (SIGTERM), once ``run`` started
        self._verify: asyncio.Task[None] | None = None
        self._next_check = 0.0  # monotonic time of the next queue-check attempt
        self._check_delay = settings.worker_poll_seconds
        self.passes = 0  # loop passes, for tests and diagnostics
        self.checks = 0  # queue-check attempts started

    async def maintain(self) -> None:
        """Reap, expire and settle once (ADR 0005 sections 3 and 5)."""
        await _quietly("reap", lambda: queue.reap(self.factory))
        await _quietly("expire", lambda: queue.expire(self.factory))
        await _quietly("settle", lambda: settle(self.factory, self.ledger))

    def verified(self) -> bool:
        """The queue check passed on a connection of this engine (ADR 0005 section 3). Until it
        has, a check runs in the background (so a database that is down never blocks the loop or
        its shutdown), at most one at a time and with backoff between attempts (from
        ``worker_poll_seconds``, doubling up to ``CHECK_BACKOFF_MAX_SECONDS``); a drift raises
        ``DatabaseRoleError`` and stops the worker."""
        if queue_verified(self.engine):
            return True
        check = self._verify
        if check is not None:
            if not check.done():
                return False
            self._verify = None
            error = None if check.cancelled() else check.exception()
            if isinstance(error, DatabaseRoleError):
                raise error
            if error is not None:
                logger.warning("queue check failed", extra={"error_type": type(error).__name__})
            if queue_verified(self.engine):
                return True
            self._next_check = time.monotonic() + self._check_delay
            self._check_delay = min(self._check_delay * 2, CHECK_BACKOFF_MAX_SECONDS)
            return False
        if time.monotonic() >= self._next_check:
            self.checks += 1
            self._verify = asyncio.ensure_future(verify_database_role(self.engine))
        return False

    def _started(self, execution: Execution) -> None:
        task = asyncio.ensure_future(self._execute(execution))
        self.running[task] = execution
        task.add_done_callback(self._ended)

    def _ended(self, task: asyncio.Task[None]) -> None:
        self.running.pop(task, None)
        self.wake.set()

    async def _execute(self, execution: Execution) -> None:
        try:
            await execute(execution, self.factory, self.registry, self.config, self.shutdown)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # left to the reaper when its lease expires
            logger.warning("job run failed", extra={"error_type": type(exc).__name__})

    async def claim_free_slots(self) -> int:
        claimed = 0
        kinds = self.registry.names()
        while (
            not self.shutdown.is_set()
            and not self.stop.is_set()
            and len(self.running) < self.settings.worker_concurrency
        ):
            row = await queue.claim(self.factory, self.id, self.settings.job_lease_seconds, kinds)
            if row is None:
                break
            claimed += 1
            self._started(Execution(row))
        return claimed

    async def _claim_step(self) -> None:
        try:
            if self.verified():
                await self.claim_free_slots()
        except asyncio.CancelledError:
            raise
        except DatabaseRoleError:
            raise  # a drifted queue or a privileged role: no override, the worker stops
        except Exception as exc:
            logger.warning("job claim failed", extra={"error_type": type(exc).__name__})

    async def run(self, stop: asyncio.Event) -> None:
        self.stop = stop
        listen_stop = asyncio.Event()
        listener = asyncio.ensure_future(listen(self.engine, self.wake, listen_stop))
        stopper = asyncio.ensure_future(self._wake_on(stop))
        now = time.monotonic()
        next_maintenance = now
        next_purge = now + self.settings.purge_interval_seconds
        try:
            while not stop.is_set():
                self.passes += 1
                await self._claim_step()
                timeout = self.settings.worker_poll_seconds
                if queue_verified(self.engine) and not stop.is_set():
                    # Maintenance and the purge run only on a verified queue, so their timers
                    # never shorten the wait while the database is down (no hot loop).
                    now = time.monotonic()
                    if now >= next_maintenance:
                        await self.maintain()
                        next_maintenance = (
                            time.monotonic() + self.settings.worker_maintenance_seconds
                        )
                    if now >= next_purge:
                        await purge_once(self.factory, self.settings)
                        next_purge = time.monotonic() + self.settings.purge_interval_seconds
                    timeout = min(
                        timeout,
                        max(next_maintenance - time.monotonic(), 0.01),
                        max(next_purge - time.monotonic(), 0.01),
                    )
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(self.wake.wait(), timeout=timeout)
                self.wake.clear()
        finally:
            listen_stop.set()
            background = [t for t in (listener, stopper, self._verify) if t is not None]
            for task in background:
                task.cancel()
            await asyncio.gather(*background, return_exceptions=True)
            await self.stop_runs()

    async def _wake_on(self, stop: asyncio.Event) -> None:
        await stop.wait()
        self.shutdown.set()  # at once: runs stop at their next checkpoint, nothing more is claimed
        self.wake.set()

    async def stop_runs(self) -> None:
        """The shutdown hand-back of ADR 0005 section 3."""
        self.shutdown.set()
        if not self.running:
            return
        tasks = list(self.running)
        executions = [self.running[t] for t in tasks]
        _, busy = await asyncio.wait(tasks, timeout=self.settings.worker_shutdown_wait_seconds)
        for task in busy:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        pending = [e for e in executions if e.needs_hand_back()]
        results = await asyncio.gather(
            *(hand_back(e, self.factory) for e in pending), return_exceptions=True
        )
        for result in results:
            if isinstance(result, BaseException):
                logger.warning("job hand-back failed", extra={"error_type": type(result).__name__})


async def run_worker(
    settings: Settings,
    stop: asyncio.Event,
    *,
    registry: KindRegistry = KINDS,
    ledger: CreditLedger | None = None,
) -> None:
    """Run until ``stop`` is set, then hand back what is still running."""
    engine = create_engine(settings, **worker_pool(settings))  # connects lazily
    try:
        logger.info("worker started")
        await Worker(settings, engine, registry=registry, ledger=ledger).run(stop)
        logger.info("worker stopped")
    finally:
        await engine.dispose()
