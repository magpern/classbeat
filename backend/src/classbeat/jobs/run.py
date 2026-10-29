"""One run of a claimed job: its checkpoints, stages, fenced writes and heartbeat.

A kind's pipeline gets a ``JobRun`` and reaches the database only through it:

- ``checkpoint()`` before each model request, each tool call, each external operation and
  between the library reads of a music step (ADR 0005 section 4). It raises ``RunStopped`` when
  the lease is gone (``lost``), the worker is shutting down (``shutdown``: no new model turn or
  external call after SIGTERM) or the user asked to cancel (``cancelled``; the fence returns
  ``cancel_requested_at``).
- ``stage(name)``: a checkpoint, then the job's ``stage`` and a ``stage_started`` event.
- ``fenced()``: a fenced transaction for the kind's own writes (revisions, ``agent_runs``,
  external operations). ``LeaseLost`` marks the run lost.
- ``external_call(timeout)``: around one provider write (X1). It starts with a checkpoint, so no
  call starts after a cancel or SIGTERM, and while it is open the call counts as in flight: a
  cancel does not interrupt it, a lost lease does not abandon it, and shutdown waits for it. The
  transaction writing its ``pending`` row extends the lease (``FencedSession.
  extend_lease_for_call``).

**For kind authors: never nest them.** No ``checkpoint()``, ``stage()`` or second ``fenced()``
inside an open ``fenced()`` block (#109 item 7): each opens its own transaction, whose fence waits
for the row lock the outer one holds, and fails after the role's 5 s ``lock_timeout``. Do the
fenced write, leave the block, then checkpoint.

The heartbeat extends the lease every lease/3 with ``GREATEST`` (it never shortens an extension
made for a call) in its own short fenced transaction. Lost (0 rows, or 3 database errors in a
row), it marks the run lost and abandons whatever is not an external call in flight.
"""

import asyncio
import logging
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from typing import Final

from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from classbeat.context import UserContext
from classbeat.jobs import transitions
from classbeat.jobs.errors import LeaseLost, RunStopped
from classbeat.jobs.fence import FencedSession, Lease, fenced_session

logger = logging.getLogger(__name__)

Factory = async_sessionmaker[AsyncSession]
HEARTBEAT_ERRORS: Final = 3


class JobRun:
    def __init__(
        self,
        factory: Factory,
        lease: Lease,
        *,
        kind: str,
        input: BaseModel,
        attempt: int,
        approved: bool,
        lease_seconds: float,
        shutdown: asyncio.Event,
    ) -> None:
        self.factory = factory
        self.lease = lease
        self.kind = kind
        self.input = input
        self.attempt = attempt
        self.approved = approved  # the post-approval phase
        self.lease_seconds = lease_seconds
        self.shutdown = shutdown
        self.lost = False
        self.cancel_requested = False
        self.in_flight = 0
        self._on_lost: Callable[[], object] | None = None

    @property
    def ctx(self) -> UserContext:
        return self.lease.ctx

    def mark_lost(self) -> None:
        if not self.lost:
            self.lost = True
            logger.warning("job lease lost", extra={"job_id": str(self.lease.job_id)})
            if self._on_lost is not None and self.in_flight == 0:
                self._on_lost()

    def on_lost(self, callback: Callable[[], object]) -> None:
        self._on_lost = callback

    @asynccontextmanager
    async def fenced(self) -> AsyncIterator[FencedSession]:
        if self.lost:
            raise LeaseLost
        try:
            async with fenced_session(self.factory, self.lease) as fs:
                yield fs
                if fs.cancel_requested:
                    self.cancel_requested = True
        except LeaseLost:
            self.mark_lost()
            raise

    async def checkpoint(self) -> None:
        if self.lost:
            raise RunStopped("lost")
        if self.shutdown.is_set():
            raise RunStopped("shutdown")
        try:
            async with self.fenced() as fs:
                cancel = fs.cancel_requested
        except LeaseLost:
            raise RunStopped("lost") from None
        if cancel:
            self.cancel_requested = True
            raise RunStopped("cancelled")

    async def stage(self, name: str) -> None:
        await self.checkpoint()
        try:
            async with self.fenced() as fs:
                await transitions.set_stage(fs, name)
        except LeaseLost:
            raise RunStopped("lost") from None

    @asynccontextmanager
    async def external_call(self, timeout_seconds: float) -> AsyncIterator[None]:
        if timeout_seconds <= 0:
            raise ValueError("a provider call needs a positive timeout")
        await self.checkpoint()
        self.in_flight += 1
        try:
            yield
        finally:
            self.in_flight -= 1
            if self.lost and self.in_flight == 0 and self._on_lost is not None:
                self._on_lost()

    async def heartbeat(self) -> None:
        """Until cancelled: extend the lease every lease/3; stop when it is lost."""
        interval = self.lease_seconds / 3
        errors = 0
        while not self.lost:
            await asyncio.sleep(interval)
            try:
                async with fenced_session(self.factory, self.lease) as fs:
                    cancel = await fs.extend_lease(self.lease_seconds, heartbeat=True)
            except LeaseLost:
                self.mark_lost()
                return
            except Exception as exc:  # the database blinked; three in a row lose the run
                errors += 1
                logger.warning("job heartbeat failed", extra={"error_type": type(exc).__name__})
                if errors >= HEARTBEAT_ERRORS:
                    self.mark_lost()
                    return
                continue
            errors = 0
            if cancel:
                self.cancel_requested = True
