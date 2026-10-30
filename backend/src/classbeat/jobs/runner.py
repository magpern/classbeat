"""From a claimed row to a settled run (ADR 0005 sections 2 to 4).

``execute`` issues the owner's context (``classbeat.jobs.issuer``), reads the job and its input
in a fenced transaction as the owner, runs the kind's pipeline with a heartbeat and records how
the attempt ended, exactly once and fenced:

- the pipeline's outcome: ``completed`` or ``waiting_for_approval``, its ``store`` in the same
  transaction (``cancelled`` instead when a cancel was requested meanwhile);
- ``RunStopped``: ``cancelled``, a hand-back on shutdown (the attempt not counted), or nothing
  when the lease is lost (the reaper owns the job then);
- a failure: retried with backoff, requeued for ``operation_busy``, or ``failed``; a
  ``JobFailure`` raised by the outcome's ``store`` rolls the store back and counts the same way
  (any other exception there is ``internal``).

The outcome's ``release`` runs once the attempt is over, on every path after the pipeline
returned it.

Only categories and exception type names are logged, never texts.
"""

import asyncio
import contextlib
import logging
from dataclasses import dataclass, field
from typing import Any

from pydantic import ValidationError
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from classbeat.db import DatabaseRoleError
from classbeat.jobs import transitions
from classbeat.jobs.errors import JobFailure, LeaseLost, RunStopped, backoff_seconds, failure_of
from classbeat.jobs.fence import Lease, fenced_session
from classbeat.jobs.issuer import Claimed, issue, owner_context
from classbeat.jobs.kinds import AwaitApproval, Complete, KindRegistry, Outcome
from classbeat.jobs.run import JobRun

logger = logging.getLogger(__name__)

Factory = async_sessionmaker[AsyncSession]

_LOAD = text(
    "SELECT kind, input, attempt, approved_at FROM jobs "
    "WHERE id = :job_id AND owner_id = :owner_id AND lease_version = :v"
)


@dataclass(frozen=True, slots=True)
class RunConfig:
    lease_seconds: int = 120
    approval_ttl_hours: int = 168
    busy_requeue_limit: int = 5


@dataclass(eq=False)
class Execution:
    """What the worker knows about one claimed job while it runs."""

    claimed: Claimed
    lease: Lease | None = None
    run: JobRun | None = None
    settled: bool = False  # its attempt was recorded, or it is not this worker's to record
    result: str | None = None  # the state written, or "lost"
    extra: dict[str, Any] = field(default_factory=dict)

    def needs_hand_back(self) -> bool:
        return not self.settled


def _log(execution: Execution, message: str, **extra: Any) -> None:
    logger.info(message, extra={"job_id": str(execution.claimed.job_id), **extra})


async def _record(execution: Execution, factory: Factory, write: Any) -> None:
    """Run ``write(fs)`` in a fenced transaction; a lost lease records nothing."""
    if execution.lease is None:
        raise RuntimeError("no lease to record on")
    try:
        async with fenced_session(factory, execution.lease) as fs:
            execution.result = await write(fs)
    except LeaseLost:
        execution.result = "lost"
    execution.settled = True


async def hand_back(execution: Execution, factory: Factory) -> None:
    """The shutdown hand-back (ADR 0005 section 3): fenced requeue, the attempt not counted.
    A run stopped before its context was issued is handed back as its bare owner (the requeue
    touches only the owner's job row)."""
    if execution.lease is None:
        claimed = execution.claimed
        execution.lease = Lease(
            claimed.job_id, claimed.lease_version, owner_context(claimed.owner_id)
        )
    await _record(execution, factory, transitions.hand_back)
    _log(execution, "job handed back", job_state=execution.result)


async def _finish(
    execution: Execution, factory: Factory, run: JobRun, outcome: Outcome, config: RunConfig
) -> None:
    async def write(fs: Any) -> str:
        if fs.cancel_requested:
            return await transitions.cancel(fs)
        if isinstance(outcome, Complete):
            revision = None if outcome.store is None else await outcome.store(fs)
            return await transitions.complete(fs, revision)
        if run.approved:  # a job waits for approval at most once
            return await transitions.fail(fs, "internal")
        revision, digest = await outcome.store(fs)
        await transitions.wait_for_approval(fs, revision, digest, config.approval_ttl_hours)
        return "waiting_for_approval"

    await _record(execution, factory, write)


async def _failed(
    execution: Execution, factory: Factory, run: JobRun, failure: JobFailure, config: RunConfig
) -> None:
    category = failure.category
    delay = backoff_seconds(run.attempt, failure.retry_after)

    async def write(fs: Any) -> str:
        if fs.cancel_requested:
            return await transitions.cancel(fs)
        if category == "operation_busy":
            return await transitions.operation_busy(fs, delay, config.busy_requeue_limit)
        return await transitions.retry(fs, category, delay)

    await _record(execution, factory, write)
    _log(execution, "job attempt failed", category=category, job_state=execution.result)


async def _pipeline(run: JobRun, kind_run: Any) -> Outcome:
    task: asyncio.Task[Outcome] = asyncio.ensure_future(kind_run(run))
    run.on_lost(task.cancel)  # a lost lease abandons the run (not an external call in flight)
    heartbeat = asyncio.ensure_future(run.heartbeat())
    returned = False
    try:
        try:
            outcome = await task
        finally:
            heartbeat.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await heartbeat
            if not task.done():
                task.cancel()
        returned = True
        return outcome
    finally:
        # The kind returned its outcome, but this was cancelled (shutdown) before handing it on:
        # nobody else will release it (#122 item 4).
        if not returned and task.done() and not task.cancelled() and task.exception() is None:
            _release(task.result())


async def execute(
    execution: Execution,
    factory: Factory,
    registry: KindRegistry,
    config: RunConfig,
    shutdown: asyncio.Event,
) -> None:
    claimed = execution.claimed
    issued = await issue(factory, claimed)
    execution.lease = issued.lease
    if not issued.active:
        await _record(execution, factory, lambda fs: transitions.fail(fs, "account_disabled"))
        return
    try:
        async with fenced_session(factory, issued.lease) as fs:
            row = (await fs.db.execute(_LOAD, issued.lease.params())).one()
    except LeaseLost:
        execution.settled, execution.result = True, "lost"
        return
    kind = registry.get(row.kind)
    if kind is None:
        await _record(execution, factory, lambda fs: transitions.fail(fs, "internal"))
        return
    try:
        parsed = kind.parse_input(row.input)
    except ValidationError:
        await _record(execution, factory, lambda fs: transitions.fail(fs, "invalid_input"))
        return
    run = JobRun(
        factory,
        issued.lease,
        kind=kind.name,
        input=parsed,
        attempt=row.attempt,
        approved=row.approved_at is not None,
        lease_seconds=config.lease_seconds,
        shutdown=shutdown,
    )
    execution.run = run
    _log(execution, "job started", kind=kind.name, attempt=row.attempt)
    try:
        outcome = await _pipeline(run, kind.run)
    except RunStopped as stopped:
        await _stopped(execution, factory, stopped.reason)
        return
    except LeaseLost:
        execution.settled, execution.result = True, "lost"
        return
    except DatabaseRoleError:
        raise  # not the job's failure: the worker stops and hands the lease back (#109 item 2)
    except asyncio.CancelledError:
        if run.fatal is not None:  # abandoned by the heartbeat's DatabaseRoleError (#140 item 6)
            raise run.fatal from None
        if run.lost and not shutdown.is_set():
            execution.settled, execution.result = True, "lost"
            return  # abandoned after the lease was lost; the reaper owns the job
        raise  # shutdown: the worker hands the lease back
    except Exception as exc:
        if run.fatal is not None:  # met by the heartbeat during a call that then failed
            raise run.fatal from None
        if run.lost:
            execution.settled, execution.result = True, "lost"
            return
        if not isinstance(exc, JobFailure):
            logger.warning("job pipeline raised", extra={"error_type": type(exc).__name__})
        await _failed(execution, factory, run, failure_of(exc), config)
        return
    if not isinstance(outcome, Complete | AwaitApproval):
        await _record(execution, factory, lambda fs: transitions.fail(fs, "internal"))
        return
    try:
        if run.fatal is not None:  # the heartbeat met it after the run's last checkpoint
            raise run.fatal
        await _finish(execution, factory, run, outcome, config)
    except JobFailure as failure:  # the kind's store refused (rolled back): its category
        await _failed(execution, factory, run, failure, config)
    except DatabaseRoleError:
        raise  # a drifted queue or a privileged role: the worker stops (#109 item 2)
    except Exception as exc:  # storing the result failed and was rolled back
        logger.warning("job store failed", extra={"error_type": type(exc).__name__})
        # A database blip (connection lost, timeout) retries the attempt, fenced; anything else
        # is ``internal`` (failure_of).
        await _failed(execution, factory, run, failure_of(exc), config)
    finally:
        _release(outcome)
    _log(execution, "job attempt ended", job_state=execution.result)


def _release(outcome: object) -> None:
    """The outcome's ``release`` once the attempt is over, whether its ``store`` ran or not (a
    cancel, a lost lease, shutdown, a failure before it; #122 item 4)."""
    release = outcome.release if isinstance(outcome, Complete | AwaitApproval) else None
    if release is None:
        return
    try:
        release()
    except Exception as exc:  # never hides how the attempt ended
        logger.warning("job outcome release failed", extra={"error_type": type(exc).__name__})


async def _stopped(execution: Execution, factory: Factory, reason: str) -> None:
    if reason == "lost":
        execution.settled, execution.result = True, "lost"
    elif reason == "shutdown":
        await hand_back(execution, factory)
    else:
        await _record(execution, factory, transitions.cancel)
        _log(execution, "job cancelled", job_state=execution.result)
