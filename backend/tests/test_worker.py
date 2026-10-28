import asyncio
import json
import logging
import os
import signal
import subprocess
import sys

import pytest

from classbeat.config import Settings
from classbeat.worker.loop import run_worker


async def test_worker_logs_start_and_stop_and_exits_when_the_stop_event_is_set(
    caplog: pytest.LogCaptureFixture,
) -> None:
    stop = asyncio.Event()
    with caplog.at_level(logging.INFO, logger="classbeat.worker.loop"):
        task = asyncio.create_task(run_worker(Settings(), stop))
        await asyncio.sleep(0.05)
        assert not task.done()
        stop.set()
        await asyncio.wait_for(task, timeout=1)
    messages = [record.getMessage() for record in caplog.records]
    assert messages == ["worker started", "worker stopped"]


async def test_worker_logs_a_heartbeat_every_interval(caplog: pytest.LogCaptureFixture) -> None:
    stop = asyncio.Event()
    settings = Settings(worker_heartbeat_seconds=0.02)
    with caplog.at_level(logging.INFO, logger="classbeat.worker.loop"):
        task = asyncio.create_task(run_worker(settings, stop))
        await asyncio.sleep(0.3)
        stop.set()
        await asyncio.wait_for(task, timeout=1)
    heartbeats = [r for r in caplog.records if r.getMessage() == "worker heartbeat"]
    assert len(heartbeats) >= 3


async def test_worker_with_the_stop_event_already_set_returns_at_once() -> None:
    stop = asyncio.Event()
    stop.set()
    await asyncio.wait_for(run_worker(Settings(), stop), timeout=1)


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX signals; the container runs Linux")
@pytest.mark.parametrize("sig", [signal.SIGTERM, signal.SIGINT])
def test_worker_process_exits_0_on_a_signal(
    sig: signal.Signals, unreachable_database_url: str
) -> None:
    # A closed port: a database that answers with a refused login would now stop the worker.
    env = {**os.environ, "CLASSBEAT_DATABASE_URL": unreachable_database_url}
    command = [sys.executable, "-m", "classbeat.worker"]
    with subprocess.Popen(  # noqa: S603 - our own interpreter and module
        command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env
    ) as process:
        try:
            assert process.stdout is not None
            first = json.loads(process.stdout.readline())  # blocks until the worker is up
            assert first["message"] == "worker started"
            process.send_signal(sig)
            assert process.wait(timeout=3) == 0
            rest = [json.loads(line) for line in process.stdout.read().splitlines()]
            assert [entry["message"] for entry in rest] == ["worker stopped"]
        finally:
            process.kill()
