"""The worker loop (ADR 0005 section 3): claim only after the queue check, LISTEN with a poll
fallback, maintenance, a poison row, and the shutdown hand-back."""

import asyncio
import contextlib
import time
import uuid
from collections.abc import AsyncIterator, Callable
from typing import Any

import pytest
from sqlalchemy import text

from authtools import make_settings
from classbeat.db import DatabaseRoleError, create_engine
from classbeat.jobs import Complete, JobRun, KindRegistry
from classbeat.worker import loop as worker_loop
from classbeat.worker.loop import Worker, worker_id
from jobcore import CountingLedger, JobsEnv, Upstream, registry_for, store_draft

pytestmark = pytest.mark.usefixtures("jenv")


@contextlib.asynccontextmanager
async def running(
    env: JobsEnv, registry: KindRegistry, **settings: Any
) -> AsyncIterator[tuple[Worker, asyncio.Event, asyncio.Task[None]]]:
    values: dict[str, Any] = {"job_lease_seconds": 60, "worker_poll_seconds": 0.1, **settings}
    config = make_settings(env.settings.database_url.get_secret_value(), **values)
    engine = create_engine(config)
    worker = Worker(config, engine, registry=registry, ledger=CountingLedger())
    stop = asyncio.Event()
    task = asyncio.ensure_future(worker.run(stop))
    try:
        yield worker, stop, task
    finally:
        stop.set()
        stopped_by_drift = (
            task.done() and not task.cancelled() and isinstance(task.exception(), DatabaseRoleError)
        )
        if not stopped_by_drift:  # a drift the test expected was raised to it already
            await asyncio.wait_for(task, 40)
        await engine.dispose()


async def until(check: Callable[[], bool], seconds: float = 10) -> None:
    deadline = time.monotonic() + seconds
    while not check():
        assert time.monotonic() < deadline, "timed out"
        await asyncio.sleep(0.05)


def done_run(calls: list[uuid.UUID] | None = None):
    async def run(job_run: JobRun) -> Complete:
        if calls is not None:
            calls.append(job_run.lease.job_id)
        await job_run.stage("pick")
        return Complete(store_draft)

    return run


def test_the_worker_id_fits_the_claim_function() -> None:
    import re

    assert re.fullmatch(r"[A-Za-z0-9._:-]{1,100}", worker_id())
    assert worker_id() != worker_id()


async def test_nothing_is_claimed_before_the_queue_check_passed(
    jenv: JobsEnv, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, job_id = jenv.job()

    async def no_verify(engine: object) -> None:
        return None

    with monkeypatch.context() as patched:  # not undo(): that would also undo the test guards
        patched.setattr(worker_loop, "queue_verified", lambda engine: False)
        patched.setattr(worker_loop, "verify_database_role", no_verify)
        async with running(jenv, registry_for(done_run())):
            await asyncio.sleep(0.5)
            assert jenv.row(job_id).state == "queued"
    async with running(jenv, registry_for(done_run())):
        await until(lambda: jenv.row(job_id).state == "completed")


def listening(env: JobsEnv) -> int:
    """Backends of the app role whose last statement was the worker's ``LISTEN``."""
    return env.sql(
        "SELECT count(*) FROM pg_stat_activity WHERE usename = 'classbeat_app' "
        "AND query = 'LISTEN jobs_queued' AND state = 'idle'"
    ).scalar_one()


async def test_a_notification_wakes_the_worker_before_its_poll(jenv: JobsEnv) -> None:
    owner = jenv.user()
    before = listening(jenv)
    async with running(jenv, registry_for(done_run()), worker_poll_seconds=30) as (worker, _, _):
        await until(lambda: listening(jenv) > before)  # the listener is up (#109 item 4)
        _, job_id = jenv.job(owner)
        started = time.monotonic()
        jenv.sql("SELECT pg_notify('jobs_queued', '')")
        await until(lambda: jenv.row(job_id).state == "completed", seconds=10)
        assert time.monotonic() - started < 10  # well before the 30 s poll
        await until(lambda: not worker.running)  # the task ends just after its commit


async def test_a_poison_row_does_not_block_other_users_and_is_failed(jenv: JobsEnv) -> None:
    _, poison = jenv.job(attempt=3, max_attempts=3)
    jenv.sql("UPDATE jobs SET available_at = now() - interval '1 hour' WHERE id = :id", id=poison)
    others = [jenv.job()[1] for _ in range(3)]
    calls: list[uuid.UUID] = []
    async with running(jenv, registry_for(done_run(calls))):
        await until(lambda: all(jenv.row(j).state == "completed" for j in others))
        await until(lambda: jenv.row(poison).state == "failed")
    assert (jenv.row(poison).error_category) == "internal"
    assert sorted(calls) == sorted(others)


async def test_maintenance_reaps_expires_and_settles(jenv: JobsEnv) -> None:
    async with running(jenv, registry_for(done_run())) as (worker, _, _):
        _, finished = jenv.job()
        await until(lambda: jenv.row(finished).state == "completed")
        await worker.maintain()
        assert jenv.row(finished).credit_state == "committed"
        assert worker.ledger.calls == [("commit", finished)]  # pyright: ignore[reportAttributeAccessIssue]


async def test_the_concurrency_limit_holds(jenv: JobsEnv) -> None:
    gate = asyncio.Event()
    peak = 0
    live = 0

    async def run(job_run: JobRun) -> Complete:
        nonlocal peak, live
        live += 1
        peak = max(peak, live)
        await gate.wait()
        live -= 1
        return Complete()

    jobs = [jenv.job()[1] for _ in range(5)]
    async with running(jenv, registry_for(run), worker_concurrency=2) as (worker, _, _):
        await until(lambda: len(worker.running) == 2)
        await asyncio.sleep(0.4)
        assert peak == 2
        gate.set()
        await until(lambda: all(jenv.row(j).state == "completed" for j in jobs))


async def test_shutdown_hands_the_lease_back_without_an_attempt_and_no_new_call(
    jenv: JobsEnv,
) -> None:
    _, job_id = jenv.job()
    upstream = Upstream()
    in_turn = asyncio.Event()

    async def run(job_run: JobRun) -> Complete:
        await job_run.checkpoint()
        await upstream.call()  # a model turn in flight when SIGTERM comes
        in_turn.set()
        await asyncio.sleep(0.3)
        async with job_run.external_call(10):  # refused: shutdown began
            await upstream.call()
        return Complete()

    async with running(jenv, registry_for(run)) as (_, stop, task):
        await asyncio.wait_for(in_turn.wait(), 10)
        lease_version = jenv.row(job_id).lease_version
        stop.set()
        await asyncio.wait_for(task, 30)
    row = jenv.row(job_id)
    assert (row.state, row.attempt, row.leased_by) == ("queued", 0, None)
    assert row.lease_version == lease_version + 1
    assert upstream.calls == 1
    assert jenv.kinds(job_id)[-1] == "requeued"


async def test_a_call_still_in_flight_after_the_wait_is_left_pending_and_handed_back(
    jenv: JobsEnv,
) -> None:
    owner, job_id = jenv.job()
    operation = uuid.uuid4()
    in_call = asyncio.Event()

    async def run(job_run: JobRun) -> Complete:
        async with job_run.external_call(30):
            async with job_run.fenced() as fs:
                await fs.db.execute(
                    text(
                        "INSERT INTO external_operations (id, owner_id, job_id, kind, "
                        "idempotency_key, request_hash, state, lease_version) VALUES (:id, :o, "
                        ":j, 'mowl_attach_spotify_playlist', 'spotify:37i9dQZF1DXcBWIGoYBM5M', "
                        "decode(repeat('ab', 32), 'hex'), 'pending', :v)"
                    ),
                    {"id": operation, "o": owner, "j": job_id, "v": job_run.lease.lease_version},
                )
                await fs.extend_lease_for_call(30)
            in_call.set()
            await asyncio.sleep(60)  # a slow provider
        return Complete()

    async with running(jenv, registry_for(run), worker_shutdown_wait_seconds=0.3) as (
        _,
        stop,
        task,
    ):
        await asyncio.wait_for(in_call.wait(), 10)
        started = time.monotonic()
        stop.set()
        await asyncio.wait_for(task, 30)
        assert time.monotonic() - started < 5
    row = jenv.row(job_id)
    assert (row.state, row.attempt, row.lease_version) == ("queued", 0, 2)
    op = jenv.sql(
        "SELECT state, lease_version FROM external_operations WHERE id = :id", id=operation
    ).one()
    assert (op.state, op.lease_version) == ("pending", 1)  # uncertain for the next holder (X1)


async def test_a_cancel_requested_before_the_hand_back_ends_the_job_cancelled(
    jenv: JobsEnv,
) -> None:
    _, job_id = jenv.job()
    in_turn = asyncio.Event()

    async def run(job_run: JobRun) -> Complete:
        in_turn.set()
        await asyncio.sleep(60)
        return Complete()

    async with running(jenv, registry_for(run), worker_shutdown_wait_seconds=0.2) as (
        _,
        stop,
        task,
    ):
        await asyncio.wait_for(in_turn.wait(), 10)
        jenv.sql("UPDATE jobs SET cancel_requested_at = now() WHERE id = :id", id=job_id)
        stop.set()
        await asyncio.wait_for(task, 30)
    row = jenv.row(job_id)
    assert (row.state, row.finished_at is not None) == ("cancelled", True)


async def test_sigterm_during_a_claim_claims_nothing_more_and_calls_nothing(
    jenv: JobsEnv, monkeypatch: pytest.MonkeyPatch
) -> None:
    jobs = [jenv.job()[1] for _ in range(2)]
    upstream = Upstream()
    real_claim = worker_loop.queue.claim
    holder: dict[str, asyncio.Event] = {}

    async def claim_then_sigterm(*args: Any, **kwargs: Any) -> Any:
        holder["stop"].set()  # SIGTERM arrives while this claim is in flight
        await asyncio.sleep(0.05)  # the signal path runs before the claim returns
        return await real_claim(*args, **kwargs)

    async def run(job_run: JobRun) -> Complete:
        async with job_run.external_call(10):
            await upstream.call()
        return Complete()

    monkeypatch.setattr(worker_loop.queue, "claim", claim_then_sigterm)
    async with running(jenv, registry_for(run), worker_concurrency=2) as (worker, stop, task):
        holder["stop"] = stop
        await asyncio.wait_for(task, 30)
        assert worker.shutdown.is_set()
    rows = [jenv.row(j) for j in jobs]
    assert upstream.calls == 0
    assert sorted(r.lease_version for r in rows) == [0, 2]  # one claimed then handed back
    assert all((r.state, r.attempt) == ("queued", 0) for r in rows)


DRIFT = "the job queue does not match ADR 0005 section 3: planted by the test"


async def test_a_drift_found_by_the_background_check_stops_the_worker(
    jenv: JobsEnv, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#109 item 2: the background queue check raising ``DatabaseRoleError`` ends ``run``."""
    _, job_id = jenv.job()

    async def drifted(engine: object) -> None:
        raise DatabaseRoleError(DRIFT)

    monkeypatch.setattr(worker_loop, "queue_verified", lambda engine: False)
    monkeypatch.setattr(worker_loop, "verify_database_role", drifted)
    async with running(jenv, registry_for(done_run())) as (worker, _, task):
        with pytest.raises(DatabaseRoleError, match="planted"):
            await asyncio.wait_for(task, 10)
        assert worker.checks == 1
    assert jenv.row(job_id).state == "queued"


async def test_a_drift_met_by_a_run_stops_the_worker_and_hands_the_leases_back(
    jenv: JobsEnv,
) -> None:
    """#109 item 2: a ``DatabaseRoleError`` on a new connection inside a run (here raised where
    the run would open one) is not the job's failure: the worker stops, no attempt counted."""
    _, refused = jenv.job()
    _, other = jenv.job()
    in_turn = asyncio.Event()

    async def run(job_run: JobRun) -> Complete:
        if job_run.lease.job_id == refused:
            await until(in_turn.is_set)
            raise DatabaseRoleError(DRIFT)
        in_turn.set()
        await asyncio.sleep(60)  # stops at shutdown
        return Complete()

    async with running(jenv, registry_for(run)) as (_, _, task):
        with pytest.raises(DatabaseRoleError, match="planted"):
            await asyncio.wait_for(task, 30)
    for job_id in (refused, other):
        row = jenv.row(job_id)
        assert (row.state, row.attempt, row.error_category) == ("queued", 0, None)
        assert jenv.kinds(job_id)[-1] == "requeued"


def handed_back(caplog: pytest.LogCaptureFixture, job_id: uuid.UUID) -> list[str]:
    return [
        getattr(r, "job_state", "")
        for r in caplog.records
        if r.getMessage() == "job handed back" and getattr(r, "job_id", None) == str(job_id)
    ]


async def test_a_drift_met_by_a_run_while_shutdown_waits_is_handed_back_once(
    jenv: JobsEnv, caplog: pytest.LogCaptureFixture
) -> None:
    """#140 item 5: a run raising ``DatabaseRoleError`` while ``stop_runs`` waits for it is both
    stranded and one of the waited runs: one hand-back, not a second that finds the lease gone."""
    _, job_id = jenv.job()
    in_turn = asyncio.Event()

    async def run(job_run: JobRun) -> Complete:
        in_turn.set()
        await until(job_run.shutdown.is_set, 30)
        raise DatabaseRoleError(DRIFT)  # its next connection, after SIGTERM

    caplog.set_level("INFO", logger="classbeat.jobs.runner")
    async with running(jenv, registry_for(run)) as (_, stop, task):
        await asyncio.wait_for(in_turn.wait(), 10)
        stop.set()
        await asyncio.wait_for(task, 30)
    assert handed_back(caplog, job_id) == ["queued"]
    row = jenv.row(job_id)
    assert (row.state, row.attempt) == ("queued", 0)
    assert jenv.kinds(job_id).count("requeued") == 1


async def test_a_drift_met_by_the_heartbeat_stops_the_worker_and_hands_the_lease_back(
    jenv: JobsEnv, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """#140 item 6: ``DatabaseRoleError`` on the heartbeat's connection is not a database blip
    (which would lose the run after three): the run is abandoned and the worker stops."""
    from classbeat.jobs import run as run_module

    _, job_id = jenv.job()
    beats = {"n": 0}
    real = run_module.fenced_session

    @contextlib.asynccontextmanager
    async def drifted(factory: Any, lease: Any) -> AsyncIterator[Any]:
        beats["n"] += 1  # the run below opens no fenced transaction: only the heartbeat does
        raise DatabaseRoleError(DRIFT)
        async with real(factory, lease) as fs:  # pragma: no cover
            yield fs

    async def run(job_run: JobRun) -> Complete:
        job_run.lease_seconds = 0.15  # read by the heartbeat, which starts after this
        await asyncio.sleep(60)  # abandoned by the heartbeat
        return Complete()

    monkeypatch.setattr(run_module, "fenced_session", drifted)
    caplog.set_level("INFO", logger="classbeat.jobs.runner")
    async with running(jenv, registry_for(run)) as (_, _, task):
        with pytest.raises(DatabaseRoleError, match="planted"):
            await asyncio.wait_for(task, 30)
    assert beats["n"] == 1  # stopped at the first refusal, not retried as a blip
    assert handed_back(caplog, job_id) == ["queued"]
    row = jenv.row(job_id)
    assert (row.state, row.attempt, row.error_category) == ("queued", 0, None)


async def test_a_drift_met_by_maintenance_stops_the_worker(
    jenv: JobsEnv, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def drifted(factory: object) -> int:
        raise DatabaseRoleError(DRIFT)

    monkeypatch.setattr(worker_loop.queue, "reap", drifted)
    async with running(jenv, registry_for(done_run())) as (_, _, task):
        with pytest.raises(DatabaseRoleError, match="planted"):
            await asyncio.wait_for(task, 10)


async def test_sigterm_cancels_a_maintenance_step_in_progress(
    jenv: JobsEnv, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#109 item 8: a slow maintenance step does not hold the shutdown."""
    in_step = asyncio.Event()
    cancelled: list[bool] = []

    async def slow(factory: object) -> int:
        in_step.set()
        try:
            await asyncio.sleep(60)
        except asyncio.CancelledError:
            cancelled.append(True)
            raise
        return 0

    monkeypatch.setattr(worker_loop.queue, "reap", slow)
    async with running(jenv, registry_for(done_run())) as (_, stop, task):
        await asyncio.wait_for(in_step.wait(), 10)
        started = time.monotonic()
        stop.set()
        await asyncio.wait_for(task, 10)
        assert time.monotonic() - started < 2
    assert cancelled == [True]
