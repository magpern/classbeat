"""The two-worker drill of ADR 0026 stage 1 (#735): two worker processes against one database, as
``docker compose up -d --scale worker=2`` runs them, and one of them killed (``docker kill``).

Worker A is a real second process (``tests/drill_worker.py``: its own interpreter, engine, pool
and worker id); worker B is a ``Worker`` in this process. With a global cap of one analysis:

- while A holds the only analysis slot, B runs every design job and none of the waiting analyses:
  a waiting analysis is claimed once without a slot, stops before analysing and goes back to the
  queue asking for one (#574), and is never claimed with one (the cap is counted in the database,
  across workers);
- A is killed with ``SIGKILL`` while holding the slot: nothing is handed back; its lease runs out,
  B's reaper requeues the job under a new token (fencing the dead holder: a write under A's token
  is refused) and B finishes it and the waiting analyses, one at a time;
- every job completes exactly once (A, while alive, may take design jobs too), never more than one
  analysis runs at a time, and every claim recorded its queue wait (``claimed.wait_ms``,
  ``analysis_slot``).

The operator's version of the drill, with containers, is in docs/developer-commands.md
("Two workers").
"""

import asyncio
import contextlib
import json
import os
import signal
import subprocess
import sys
import time
from collections.abc import AsyncIterator, Callable
from pathlib import Path
from typing import Any

import pytest

from authtools import make_settings
from classbeat.db import create_engine
from classbeat.jobs import Complete, JobRun, KindRegistry, LeaseLost, fenced_session
from classbeat.jobs.fence import Lease
from classbeat.jobs.issuer import owner_context
from classbeat.worker.loop import Worker
from jobcore import CountingLedger, JobsEnv, KindInput, registry_for

pytestmark = pytest.mark.usefixtures("jenv")

TESTS = Path(__file__).resolve().parent
ANALYSE = json.dumps({"note": "analyse"})


async def until(check: Callable[[], bool], seconds: float = 30) -> None:
    deadline = time.monotonic() + seconds
    while not check():
        assert time.monotonic() < deadline, "timed out"
        await asyncio.sleep(0.05)


class Analyses:
    """Worker B's kind: an ``analyse`` job takes a slot and analyses briefly, any other job
    designs. Counts how many analyses run at once in this process."""

    def __init__(self) -> None:
        self.live = 0
        self.peak = 0
        self.runs: list[Any] = []

    async def run(self, job_run: JobRun) -> Complete:
        given = job_run.input
        assert isinstance(given, KindInput)
        await job_run.stage("pick")
        if given.note == "analyse":
            job_run.require_analysis_slot()
            self.live += 1
            self.peak = max(self.peak, self.live)
            try:
                await asyncio.sleep(0.3)
                async with job_run.fenced() as fs:
                    await job_run.release_analysis_slot(fs)
            finally:
                self.live -= 1
            job_run.slot_released()
        self.runs.append(job_run.lease.job_id)
        return Complete()

    def registry(self) -> KindRegistry:
        return registry_for(self.run)


@contextlib.asynccontextmanager
async def worker_b(env: JobsEnv, registry: KindRegistry) -> AsyncIterator[Worker]:
    config = make_settings(
        env.settings.database_url.get_secret_value(),
        job_lease_seconds=30,
        worker_poll_seconds=0.1,
        worker_maintenance_seconds=0.2,
        worker_concurrency=3,
        mix_analysis_concurrency=1,
    )
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


def start_worker_a(env: JobsEnv) -> subprocess.Popen[str]:
    environment = {
        **os.environ,
        "DRILL_DATABASE_URL": env.settings.database_url.get_secret_value(),
        "PYTHONPATH": os.pathsep.join([str(TESTS), os.environ.get("PYTHONPATH", "")]),
    }
    return subprocess.Popen(  # noqa: S603 - our own interpreter and our own script
        [sys.executable, str(TESTS / "drill_worker.py")],
        env=environment,
        stdout=subprocess.PIPE,
        text=True,
    )


def waits_for_slot(env: JobsEnv, job_id: Any) -> bool:
    """The job's first pass, without a slot, has ended: it is back in the queue asking for one."""
    row = env.row(job_id)
    return row.state == "queued" and row.analysis_slot


def slot_rows(env: JobsEnv) -> int:
    return env.sql(
        "SELECT count(*) FROM jobs WHERE state = 'running' AND analysis_slot"
    ).scalar_one()


@pytest.mark.skipif(not hasattr(signal, "SIGKILL"), reason="needs SIGKILL")
async def test_two_workers_keep_the_cap_and_a_killed_workers_job_is_reaped_and_fenced(
    jenv: JobsEnv,
) -> None:
    kind = Analyses()
    held = jenv.job(input=ANALYSE, analysis_slot=True)[1]
    process = start_worker_a(jenv)
    try:
        assert process.stdout is not None
        a_id = (await asyncio.to_thread(process.stdout.readline)).strip()
        assert a_id
        await until(lambda: jenv.row(held).state == "running", 60)
        a_row = jenv.row(held)
        assert (a_row.leased_by, a_row.analysis_slot) == (a_id, True)
        sampled: list[int] = []

        def sample(check: Callable[[], bool]) -> Callable[[], bool]:
            def wrapped() -> bool:
                sampled.append(slot_rows(jenv))
                return check()

            return wrapped

        async with worker_b(jenv, kind.registry()) as b:
            assert b.id != a_id
            waiting = [jenv.job(input=ANALYSE)[1] for _ in range(3)]
            designs = [jenv.job()[1] for _ in range(3)]
            # Wait on the state, not on time: the design jobs done and every waiting analysis
            # back in the queue asking for a slot. Its first claim has none and stops it before
            # it analyses (#574); that pass may still be running when the designs are done.
            await until(
                sample(
                    lambda: (
                        all(jenv.row(j).state == "completed" for j in designs)
                        and all(waits_for_slot(jenv, j) for j in waiting)
                    )
                )
            )
            # A holds the only slot: B ran every design job and none of the analyses, and no
            # waiting analysis was claimed with a slot (one claim without, one requeue for it).
            assert [jenv.row(j).state for j in waiting] == ["queued"] * 3
            assert not set(waiting) & set(kind.runs)
            for job_id in waiting:
                passes = [
                    (k, p) for _, k, p, _ in jenv.events(job_id) if k in {"claimed", "requeued"}
                ]
                assert [(k, p.get("analysis_slot"), p.get("reason")) for k, p in passes] == [
                    ("claimed", False, None),
                    ("requeued", None, "analysis_slot"),
                ]
            assert jenv.row(held).leased_by == a_id

            process.send_signal(signal.SIGKILL)  # docker kill: no hand-back, no last write
            await asyncio.to_thread(process.wait, 30)
            assert jenv.row(held).state == "running"  # nothing was handed back
            stale = Lease(held, a_row.lease_version, owner_context(a_row.owner_id))
            # The lease runs out (30 s; shortened here), B's reaper requeues it with backoff
            # (shortened too), and B claims it again holding the slot.
            jenv.expire_lease(held)
            await until(lambda: jenv.row(held).state != "running")
            jenv.sql("UPDATE jobs SET available_at = now() WHERE id = :id", id=held)
            everything = [held, *waiting, *designs]
            await until(
                sample(lambda: all(jenv.row(j).state == "completed" for j in everything)), 60
            )

        # The dead holder is fenced: a write under its token is refused.
        with pytest.raises(LeaseLost):
            async with fenced_session(jenv.factory, stale):
                pass
        assert jenv.row(held).lease_version > a_row.lease_version
        # The cap held across both workers; every job completed exactly once. Every analysis ran
        # in B (A, while alive, may also have taken design jobs: both claim every kind).
        assert max(sampled) <= 1
        assert kind.peak == 1
        assert set(kind.runs) >= {held, *waiting}
        assert len(kind.runs) == len(set(kind.runs))
        for job_id in (held, *waiting, *designs):
            assert jenv.kinds(job_id).count("completed") == 1
        assert jenv.kinds(held).count("retry_scheduled") == 1  # the reaper's requeue
        # Every claim recorded its wait; the second claim of the reaped job holds the slot.
        claims = [
            (p["analysis_slot"], p["wait_ms"]) for _, k, p, _ in jenv.events(held) if k == "claimed"
        ]
        assert [slot for slot, _ in claims] == [True, True]
        assert all(wait >= 0 for _, wait in claims)
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(30)
        if process.stdout is not None:
            process.stdout.close()
