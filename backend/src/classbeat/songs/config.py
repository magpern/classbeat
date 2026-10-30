"""The song-picking settings both processes give the kinds (ADR 0008 section 10).

The web app (``admit``: the feature switch) and the worker (the job ceiling, the pool's sync wait)
each call ``configure`` at start (``classbeat.web``, ``classbeat.worker.__main__``). Without it the
defaults apply, and the feature is **off**: ``admit`` refuses every pick with ``song_picking_off``.
"""

from dataclasses import dataclass
from typing import Final

from classbeat.config import Settings

DEFAULT_COST_MICROS: Final = 3_000_000
DEFAULT_SYNC_WAIT_SECONDS: Final = 120.0


@dataclass(frozen=True, slots=True)
class SongConfig:
    enabled: bool = False  # CLASSBEAT_SONG_PICKING
    cost_micros: int = DEFAULT_COST_MICROS  # CLASSBEAT_SONG_JOB_COST_MICROS
    sync_wait_seconds: float = DEFAULT_SYNC_WAIT_SECONDS  # ..._SPOTIFY_POOL_SYNC_WAIT_SECONDS

    @classmethod
    def from_settings(cls, settings: Settings) -> "SongConfig":
        return cls(
            enabled=settings.song_picking,
            cost_micros=settings.song_job_cost_micros,
            sync_wait_seconds=settings.spotify_pool_sync_wait_seconds,
        )


_config = SongConfig()


def configure(config: SongConfig | None) -> None:
    """Set the settings (None: the defaults, off)."""
    global _config
    _config = config or SongConfig()


def current() -> SongConfig:
    return _config
