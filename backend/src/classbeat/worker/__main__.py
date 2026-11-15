"""``python -m classbeat.worker``: run the worker until SIGTERM or SIGINT."""

import asyncio
import logging
import signal
import sys

from classbeat import (
    design,
    player_send,
    playlists,
    quota,
    saving,
    songs,
    swedebeat_handoff,
    swedebeat_match,
)
from classbeat.auth import EventSigner, EventSignerRefused, load_event_signer
from classbeat.config import (
    ModelProviderRefused,
    Settings,
    check_worker_has_no_paddle_secret,
    check_worker_has_no_turnstile_secret,
    check_worker_model_provider,
    worker_model_providers,
)
from classbeat.crypto.keks import load_keyring
from classbeat.db import (
    create_engine,
    create_session_factory,
    event_loop_factory,
    verify_database_role,
)
from classbeat.design import music_kind, sport
from classbeat.design.mixfetch import sweep_work_root
from classbeat.egress import install_player_allowlist, install_worker_allowlist
from classbeat.egress_gateway import install_swedebeat_gateway_origin
from classbeat.log import configure_logging
from classbeat.player.delivery import run_player_delivery
from classbeat.playlists.runtime import playlists_runtime
from classbeat.swedebeat_studio import config as studio_config
from classbeat.swedebeat_studio import kind as studio_kind  # noqa: F401 - registers studio_songs
from classbeat.worker.loop import run_worker, worker_id


def _install_signal_handlers(loop: asyncio.AbstractEventLoop, stop: asyncio.Event) -> None:
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop.set)
        except NotImplementedError:  # Windows event loops
            signal.signal(sig, lambda *_: loop.call_soon_threadsafe(stop.set))


async def _run(settings: Settings, signer: EventSigner | None = None) -> None:
    engine = create_engine(settings)
    try:
        await verify_database_role(engine)  # refuses to start on a privileged role
        # The KEK check of ADR 0004 section 2: no valid connection_kek, no worker.
        keyring = await load_keyring(create_session_factory(engine), settings.connection_kek_id)
    finally:
        await engine.dispose()
    # The job kinds' runtime (design_draft, D1): the key ring and a new model provider per run.
    design.configure(design.runtime_from_settings(settings, keyring))
    # A queued running design or revise checks the running switch again (ADR 0018 section 5);
    # coverage is the model's (#509).
    sport.configure(sport.SportGate.from_settings(settings))
    # A queued soundless design checks the soundless switch again (#527); coverage is the model's.
    music_kind.configure(music_kind.SoundlessGate.from_settings(settings))
    # The environment's plan bounds (#496): a job stored without gates is run under the current
    # plan's, whose class length cap is bounded by ``CLASSBEAT_CLASS_MINUTES_MAX`` as at admit.
    quota.configure(settings)
    # save_workout (SV1): the key ring, and the listing lag its reconciler may rely on.
    saving.configure(saving.Runtime(keyring))
    saving.configure_listing_lag(settings.mowl_listing_lag_seconds)
    # Send to player and the removal (ADR 0034 section 5): the key ring; the player's client is
    # the installed host's (``install_player_allowlist`` in ``main``), refused when it is off.
    player_send.configure(player_send.Runtime(keyring))
    # The Spotify writes (OP1): the refresh client and margin, and the listing lag of the create
    # reconciler (ADR 0008 section 2).
    playlists.configure(playlists_runtime(settings))
    # design_with_songs (SJ1): the job ceiling and the pool's sync wait; it shares design's runtime.
    songs.configure(songs.SongConfig.from_settings(settings))
    # swedebeat_match (B1): the Swedebeat switch (off unless the worker's environment has it too:
    # a match then ends feature_off) and the key ring for the Swedebeat token.
    swedebeat_match.configure(swedebeat_match.MatchConfig.from_settings(settings, keyring))
    # swedebeat_verify_mix (B2): the switch again, and the key ring that opens MOWL.
    swedebeat_handoff.configure(swedebeat_handoff.HandoffConfig.from_settings(settings, keyring))
    # studio_songs (ADR 0036): CLASSBEAT_SWEDEBEAT_STUDIO (off unless the worker has it too: a
    # queued job then ends feature_off); it shares swedebeat_match's gateway and key ring.
    studio_config.configure(studio_config.StudioConfig.from_settings(settings))
    stop = asyncio.Event()
    # Before "worker started" is logged, so a signal after that line is always handled.
    _install_signal_handlers(asyncio.get_running_loop(), stop)
    # The player events beside the jobs: maintained always, delivered with a signer.
    delivery = asyncio.create_task(run_player_delivery(settings, signer, worker_id(), stop))
    try:
        await run_worker(settings, stop)
    finally:
        stop.set()
        await delivery


logger = logging.getLogger("classbeat.worker")


def main() -> int:
    # Before anything else: every urllib request of the process (the provider library's) goes
    # through the outbound allowlist (ADR 0006 §9).
    # Without a model host until the settings say which providers the worker can use.
    install_worker_allowlist()
    settings = Settings()
    configure_logging(settings.log_level)
    try:
        check_worker_model_provider(settings)
        check_worker_has_no_turnstile_secret(settings)  # web only (ADR 0025 section 7)
        check_worker_has_no_paddle_secret(settings)  # web only (ADR 0032 section 3)
    except ModelProviderRefused as refused:
        logger.error("worker refused to start", extra={"reason": str(refused)})
        return 2
    # ADR 0012 §2: a model host only for a provider with its key (DeepSeek: and its setting on).
    install_worker_allowlist(worker_model_providers(settings))
    # ADR 0034 section 2: the player's one host (a checked setting) and the logout-token signer,
    # only when the player is configured; without its client id or key set, no worker.
    install_player_allowlist(settings.player_base_url)
    # ADR 0035 section 1: the SwedeBeat gateway's one origin (a checked setting), for the match.
    install_swedebeat_gateway_origin(settings.swedebeat_gateway_url)
    try:
        signer = None if settings.player_base_url is None else load_event_signer(settings)
    except EventSignerRefused as refused:
        logger.error("worker refused to start", extra={"reason": str(refused)})
        return 2
    # A crashed run may have left the audio of a Swedebeat mix in the temp directory (ADR 0016 §4).
    sweep_work_root()
    asyncio.run(_run(settings, signer), loop_factory=event_loop_factory())
    return 0


if __name__ == "__main__":
    sys.exit(main())
