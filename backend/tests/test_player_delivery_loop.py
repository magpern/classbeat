"""The player delivery loop with the database down (ADR 0034 section 2, #1058): it carries on,
logs a warning with the error type only (no ``ERROR``, no traceback), backs off, and ends at
``stop``. No database: the session factory fails on every call."""

import asyncio
import logging
from typing import Any, cast

import pytest

from classbeat.player.delivery import run_delivery, run_player_delivery


class Down:
    def __init__(self) -> None:
        self.calls = 0

    def __call__(self) -> Any:
        self.calls += 1
        raise ConnectionRefusedError("database down")


async def test_a_failing_round_is_a_warning_and_the_loop_backs_off(
    caplog: pytest.LogCaptureFixture,
) -> None:
    factory = Down()
    stop = asyncio.Event()
    with caplog.at_level(logging.DEBUG, logger="classbeat.player"):
        task = asyncio.create_task(
            run_delivery(cast(Any, factory), None, "w1", stop, poll_seconds=0.01)
        )
        await asyncio.sleep(0.6)
        assert not task.done()
        stop.set()
        await asyncio.wait_for(task, timeout=5)
    # 0.01, 0.02, 0.04 ... seconds: a handful of rounds, not sixty.
    assert 2 <= factory.calls <= 8
    records = [r for r in caplog.records if r.name == "classbeat.player"]
    assert records
    assert all(r.levelno == logging.WARNING for r in records)
    assert all(r.exc_info is None for r in records)
    assert {getattr(r, "error_type", None) for r in records} == {"ConnectionRefusedError"}
    assert all("database down" not in r.getMessage() for r in records)


async def test_with_stop_already_set_nothing_is_opened() -> None:
    stop = asyncio.Event()
    stop.set()
    await asyncio.wait_for(run_player_delivery(cast(Any, None), None, "w1", stop), timeout=1)
