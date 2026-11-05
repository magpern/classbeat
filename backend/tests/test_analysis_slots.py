"""The separate cap on concurrent mix analyses (issue #574): a run that must analyse and holds no
slot is requeued with one (the attempt not counted, a ``requeued`` event with the reason),
``queue_claim`` claims such a job only while a slot is free, and the slot is freed in the
transaction that stores the analysis. The claim SQL itself is in ``test_queue_functions.py``."""

import asyncio
import contextlib
import json
import time
import uuid
from collections.abc import AsyncIterator, Callable
from typing import Any

import pytest

from authtools import make_settings
from classbeat.db import create_engine
from classbeat.jobs import Complete, JobRun, KindRegistry
from classbeat.jobs.events import validated_payload
from classbeat.worker.loop import Worker
from jobcore import CountingLedger, JobsEnv, KindInput, registry_for, run_next

pytestmark = pytest.mark.usefixtures("jenv")

ANALYSE = json.dumps({"note": "analyse"})


async def until(check: Callable[[], bool], seconds: float = 20) -> None:
    deadline = time.monotonic() + seconds
    while not check():
        assert time.monotonic() < deadline, "timed out"
        await asyncio.sleep(0.02)


def slot_rows(env: JobsEnv) -> int:
    return env.sql(
        "SELECT count(*) FROM jobs WHERE state = 'running' AND analysis_slot"
    ).scalar_one()


class Analyses:
    """A test kind: ``note == "analyse"`` analyses (needs a slot, frees it once stored, then
    designs on), any other note is a design job on a playlist."""

    def __init__(self, analysis_seconds: float = 0.0, design_seconds: float = 0.0) -> None:
        self.analysis_seconds = analysis_seconds
        self.design_seconds = design_seconds
        self.live = 0  # runs inside the analysis right now
        self.peak = 0
        self.analysed: list[uuid.UUID] = []
        self.designed: list[uuid.UUID] = []
        self.slots_seen: list[bool] = []

    async def run(self, job_run: JobRun) -> Complete:
        given = job_run.input
        assert isinstance(given, KindInput)
        await job_run.stage("pick")
        if given.note == "analyse":
            self.slots_seen.append(job_run.analysis_slot)
            job_run.require_analysis_slot()
            self.live += 1
            self.peak = max(self.peak, self.live)
            try:
                await asyncio.sleep(self.analysis_seconds)
                async with job_run.fenced() as fs:
                    await job_run.release_analysis_slot(fs)
            finally:
                self.live -= 1
            job_run.slot_released()
            self.analysed.append(job_run.lease.job_id)
        else:
            self.designed.append(job_run.lease.job_id)
        await asyncio.sleep(self.design_seconds)
        return Complete()

    def registry(self) -> KindRegistry:
        return registry_for(self.run)


async def test_a_run_without_a_slot_is_requeued_with_one_and_the_attempt_not_counted(
    jenv: JobsEnv,
) -> None:
    kind = Analyses()
    _, job_id = jenv.job(input=ANALYSE)
    await run_next(jenv, kind.registry())
    row = jenv.row(job_id)
    assert (row.state, row.attempt, row.analysis_slot) == ("queued", 0, True)
    assert (row.lease_version, row.leased_by, row.lease_expires_at) == (2, None, None)
    assert row.available_at <= jenv.sql("SELECT now()").scalar_one()
    # recorded under the token the run held, as every requeue
    assert jenv.events(job_id)[-1] == (3, "requeued", {"reason": "analysis_slot"}, 1)
    assert kind.analysed == []  # stopped before anything of the analysis

    await run_next(jenv, kind.registry())  # claimed again, holding the slot
    row = jenv.row(job_id)
    assert (row.state, row.attempt, row.analysis_slot) == ("completed", 1, False)
    assert kind.slots_seen == [False, True]
    assert kind.analysed == [job_id]
    assert jenv.kinds(job_id) == [
        "claimed", "stage_started", "requeued",
        "claimed", "stage_started", "completed",
    ]  # fmt: skip


async def test_a_cancel_requested_before_the_requeue_cancels_the_job(jenv: JobsEnv) -> None:
    _, job_id = jenv.job(input=ANALYSE)

    async def run(job_run: JobRun) -> Complete:
        jenv.sql("UPDATE jobs SET cancel_requested_at = now() WHERE id = :id", id=job_id)
        job_run.require_analysis_slot()
        return Complete()

    await run_next(jenv, registry_for(run))
    row = jenv.row(job_id)
    assert (row.state, row.analysis_slot) == ("cancelled", False)
    assert jenv.kinds(job_id)[-1] == "cancelled"


async def test_a_lost_lease_writes_no_requeue(jenv: JobsEnv) -> None:
    _, job_id = jenv.job(input=ANALYSE)

    async def run(job_run: JobRun) -> Complete:
        jenv.sql("UPDATE jobs SET lease_version = lease_version + 1 WHERE id = :id", id=job_id)
        job_run.require_analysis_slot()
        return Complete()

    execution = await run_next(jenv, registry_for(run))
    assert execution.result == "lost"
    row = jenv.row(job_id)
    assert (row.state, row.analysis_slot) == ("running", False)  # the reaper's job now
    assert "requeued" not in jenv.kinds(job_id)


async def test_a_job_holding_a_slot_runs_on_and_frees_it_only_once_stored(jenv: JobsEnv) -> None:
    _, job_id = jenv.job(input=ANALYSE, analysis_slot=True)
    seen: list[bool] = []

    async def run(job_run: JobRun) -> Complete:
        job_run.require_analysis_slot()  # holds one: no requeue
        with contextlib.suppress(RuntimeError):
            async with job_run.fenced() as fs:
                await job_run.release_analysis_slot(fs)
                raise RuntimeError("the store failed")  # rolled back with the release
        seen.append(jenv.row(job_id).analysis_slot)
        async with job_run.fenced() as fs:
            await job_run.release_analysis_slot(fs)
        job_run.slot_released()
        seen.append(jenv.row(job_id).analysis_slot)
        return Complete()

    await run_next(jenv, registry_for(run))
    assert seen == [True, False]
    assert jenv.row(job_id).state == "completed"
    assert "requeued" not in jenv.kinds(job_id)


async def test_the_release_refuses_another_runs_session(jenv: JobsEnv) -> None:
    _, first = jenv.job(input=ANALYSE, analysis_slot=True)
    held: list[JobRun] = []

    async def run(job_run: JobRun) -> Complete:
        if held:
            async with job_run.fenced() as fs:
                with pytest.raises(ValueError, match="not this run's"):
                    await held[0].release_analysis_slot(fs)
        held.append(job_run)
        return Complete()

    await run_next(jenv, registry_for(run))
    jenv.job(input=ANALYSE, analysis_slot=True)
    await run_next(jenv, registry_for(run))
    assert jenv.row(first).analysis_slot is True  # never released by anyone


def test_the_requeued_event_takes_only_the_known_reason() -> None:
    assert validated_payload("requeued", {}) == {}
    assert validated_payload("requeued", {"reason": "analysis_slot"}) == {"reason": "analysis_slot"}
    for bad in ({"reason": "other"}, {"reason": "analysis_slot", "extra": 1}):
        with pytest.raises(ValueError, match="requeued"):
            validated_payload("requeued", bad)


@contextlib.asynccontextmanager
async def worker(env: JobsEnv, registry: KindRegistry, **settings: Any) -> AsyncIterator[Worker]:
    values: dict[str, Any] = {"job_lease_seconds": 60, "worker_poll_seconds": 0.1, **settings}
    config = make_settings(env.settings.database_url.get_secret_value(), **values)
    engine = create_engine(config)
    made = Worker(config, engine, registry=registry, ledger=CountingLedger())
    stop = asyncio.Event()
    task = asyncio.ensure_future(made.run(stop))
    try:
        yield made
    finally:
        stop.set()
        await asyncio.wait_for(task, 40)
        await engine.dispose()


async def test_extra_analyses_wait_while_other_users_designs_run_and_all_finish(
    jenv: JobsEnv,
) -> None:
    """Four analyses (four users) and three design jobs of other users, created after them, on
    two workers of three slots each with a cap of one analysis: the design jobs all finish
    while analyses still wait, never more than one analysis runs, and every job completes."""
    kind = Analyses(analysis_seconds=0.6, design_seconds=0.05)
    analyses = [jenv.job(input=ANALYSE)[1] for _ in range(4)]
    designs = [jenv.job()[1] for _ in range(3)]
    sampled: list[int] = []
    waiting_when_designs_done: list[int] = []

    def designs_done() -> bool:
        done = all(jenv.row(j).state == "completed" for j in designs)
        if done and not waiting_when_designs_done:
            waiting_when_designs_done.append(sum(jenv.row(j).state == "queued" for j in analyses))
        return done

    def all_done() -> bool:
        sampled.append(slot_rows(jenv))
        designs_done()
        return all(jenv.row(j).state == "completed" for j in (*analyses, *designs))

    registry = kind.registry()
    async with (
        worker(jenv, registry, worker_concurrency=3, mix_analysis_concurrency=1),
        worker(jenv, registry, worker_concurrency=3, mix_analysis_concurrency=1),
    ):
        await until(all_done, seconds=40)

    assert kind.peak == 1
    assert max(sampled) <= 1
    assert waiting_when_designs_done
    assert waiting_when_designs_done[0] >= 2
    assert sorted(kind.designed) == sorted(designs)
    assert sorted(kind.analysed) == sorted(analyses)
    for job_id in analyses:
        row = jenv.row(job_id)
        assert (row.attempt, row.analysis_slot) == (1, False)  # the requeue cost no attempt
        assert ("requeued", {"reason": "analysis_slot"}) in [
            (kind_, payload) for _, kind_, payload, _ in jenv.events(job_id)
        ]


async def test_a_cap_of_two_runs_two_analyses_at_once(jenv: JobsEnv) -> None:
    kind = Analyses(analysis_seconds=0.5)
    analyses = [jenv.job(input=ANALYSE)[1] for _ in range(4)]
    async with worker(jenv, kind.registry(), worker_concurrency=4, mix_analysis_concurrency=2):
        await until(lambda: all(jenv.row(j).state == "completed" for j in analyses), 30)
    assert kind.peak == 2
