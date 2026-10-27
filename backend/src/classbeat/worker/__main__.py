"""``python -m classbeat.worker``: run the worker until SIGTERM or SIGINT."""

import asyncio
import signal
import sys

from classbeat.config import Settings
from classbeat.db import create_engine, event_loop_factory, verify_database_role
from classbeat.log import configure_logging
from classbeat.worker.loop import run_worker


def _install_signal_handlers(loop: asyncio.AbstractEventLoop, stop: asyncio.Event) -> None:
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop.set)
        except NotImplementedError:  # Windows event loops
            signal.signal(sig, lambda *_: loop.call_soon_threadsafe(stop.set))


async def _run(settings: Settings) -> None:
    engine = create_engine(settings)
    try:
        await verify_database_role(engine, settings)  # refuses to start on a privileged role
    finally:
        await engine.dispose()
    stop = asyncio.Event()
    # Before "worker started" is logged, so a signal after that line is always handled.
    _install_signal_handlers(asyncio.get_running_loop(), stop)
    await run_worker(settings, stop)


def main() -> int:
    settings = Settings()
    configure_logging(settings.log_level)
    asyncio.run(_run(settings), loop_factory=event_loop_factory())
    return 0


if __name__ == "__main__":
    sys.exit(main())
