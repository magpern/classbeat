"""A second worker process for the two-worker drill (``test_two_workers.py``, #735).

Started by the test with ``sys.executable`` and killed with ``SIGKILL``, as ``docker kill`` ends a
container of ``docker compose up --scale worker=2``: its own interpreter, engine, pool and worker
id against the test's database. It runs the real ``classbeat.worker.loop.Worker`` with the drill's
test kind: a job with the note ``analyse`` takes a mix-analysis slot and then sleeps (holding the
slot, its lease kept alive by the heartbeat) until the process is killed; any other job completes.
It prints its worker id once and only talks to the loopback database named in
``DRILL_DATABASE_URL`` (no other network).
"""

import asyncio
import os
import sys

from authtools import make_settings
from classbeat.db import create_engine
from classbeat.jobs import Complete, JobRun
from classbeat.worker.loop import Worker
from jobcore import CountingLedger, KindInput, registry_for


async def _run(job_run: JobRun) -> Complete:
    given = job_run.input
    assert isinstance(given, KindInput)
    await job_run.stage("pick")
    if given.note == "analyse":
        job_run.require_analysis_slot()
        await asyncio.sleep(3600)  # killed here, holding the slot
    return Complete()


async def main() -> None:
    settings = make_settings(
        os.environ["DRILL_DATABASE_URL"],
        job_lease_seconds=30,
        worker_poll_seconds=0.1,
        worker_concurrency=2,
        mix_analysis_concurrency=1,
    )
    engine = create_engine(settings)
    worker = Worker(settings, engine, registry=registry_for(_run), ledger=CountingLedger())
    sys.stdout.write(worker.id + "\n")
    sys.stdout.flush()
    await worker.run(asyncio.Event())


if __name__ == "__main__":
    asyncio.run(main())
