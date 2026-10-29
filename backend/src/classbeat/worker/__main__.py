"""``python -m classbeat.worker``: run the worker until SIGTERM or SIGINT."""

import asyncio
import logging
import signal
import sys

from classbeat import design, saving
from classbeat.config import ModelProviderRefused, Settings, check_worker_model_provider
from classbeat.crypto.keks import load_keyring
from classbeat.db import (
    create_engine,
    create_session_factory,
    event_loop_factory,
    verify_database_role,
)
from classbeat.egress import install_worker_allowlist
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
        await verify_database_role(engine)  # refuses to start on a privileged role
        # The KEK check of ADR 0004 section 2: no valid connection_kek, no worker.
        keyring = await load_keyring(create_session_factory(engine), settings.connection_kek_id)
    finally:
        await engine.dispose()
    # The job kinds' runtime (design_draft, D1): the key ring and a new model provider per run.
    design.configure(design.runtime_from_settings(settings, keyring))
    # save_workout (SV1): the key ring, and the listing lag its reconciler may rely on.
    saving.configure(saving.Runtime(keyring))
    saving.configure_listing_lag(settings.mowl_listing_lag_seconds)
    stop = asyncio.Event()
    # Before "worker started" is logged, so a signal after that line is always handled.
    _install_signal_handlers(asyncio.get_running_loop(), stop)
    await run_worker(settings, stop)


logger = logging.getLogger("classbeat.worker")


def main() -> int:
    # Before anything else: every urllib request of the process (the provider library's) goes
    # through the outbound allowlist (ADR 0006 §9).
    install_worker_allowlist()
    settings = Settings()
    configure_logging(settings.log_level)
    try:
        check_worker_model_provider(settings)
    except ModelProviderRefused as refused:
        logger.error("worker refused to start", extra={"reason": str(refused)})
        return 2
    asyncio.run(_run(settings), loop_factory=event_loop_factory())
    return 0


if __name__ == "__main__":
    sys.exit(main())
