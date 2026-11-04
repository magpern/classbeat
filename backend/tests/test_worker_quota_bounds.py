"""The worker applies the environment's plan bounds to the jobs it runs (#496, #510).

A job stored without gates is run under the current plan's, whose class length cap is bounded by
``CLASSBEAT_CLASS_MINUTES_MAX`` as at admit; ``quota.configure(settings)`` in the worker's start-up
is what puts the environment's bound in force there. No database: the start-up steps around it
are replaced.
"""

import asyncio
from collections.abc import Iterator
from typing import Any

import pytest

from classbeat import quota
from classbeat.config import Settings
from classbeat.worker import __main__ as worker_main


class _Engine:
    async def dispose(self) -> None:
        return None


async def _noop(*_args: Any, **_kwargs: Any) -> None:
    return None


@pytest.fixture
def reset_bounds() -> Iterator[None]:
    quota.configure(None)
    yield
    quota.configure(None)


async def test_the_worker_applies_the_environment_length_bound(
    monkeypatch: pytest.MonkeyPatch, reset_bounds: None
) -> None:
    seen: list[int] = []

    async def run_worker(_settings: Settings, _stop: asyncio.Event) -> None:
        seen.append(quota.current().class_minutes_max)

    def nothing(*_args: Any, **_kwargs: Any) -> None:
        return None

    patch = monkeypatch.setattr
    patch(worker_main, "create_engine", lambda _settings: _Engine())
    patch(worker_main, "verify_database_role", _noop)
    patch(worker_main, "create_session_factory", nothing)
    patch(worker_main, "load_keyring", _noop)
    patch(worker_main, "run_worker", run_worker)
    patch(worker_main, "_install_signal_handlers", nothing)
    patch(worker_main, "playlists_runtime", nothing)
    for module, names in (
        (worker_main.design, ("configure", "runtime_from_settings")),
        (worker_main.sport, ("configure",)),
        (worker_main.music_kind, ("configure",)),
        (worker_main.saving, ("configure", "configure_listing_lag")),
        (worker_main.playlists, ("configure",)),
        (worker_main.songs, ("configure",)),
        (worker_main.swedebeat_handoff, ("configure",)),
        (worker_main.swedebeat_match, ("configure",)),
    ):
        for name in names:
            patch(module, name, nothing)
    patch(worker_main.sport.SportGate, "from_settings", classmethod(nothing))
    patch(worker_main.music_kind.SoundlessGate, "from_settings", classmethod(nothing))
    patch(worker_main.songs.SongConfig, "from_settings", classmethod(nothing))
    patch(worker_main.swedebeat_match.MatchConfig, "from_settings", classmethod(nothing))
    patch(worker_main.swedebeat_handoff.HandoffConfig, "from_settings", classmethod(nothing))

    settings = Settings(environment="test", class_minutes_max=60)
    assert quota.current().class_minutes_max == 180  # the default, before the worker starts
    await worker_main._run(settings)  # pyright: ignore[reportPrivateUsage]

    assert seen == [60]
