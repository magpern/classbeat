"""The worker's setting of the Spotify song stage's spotdown match (ADR 0040 amendment "spotdown is
the only analysis path"; SD-P6 stage A, #1303, #1354).

The worker calls ``configure`` at start (``classbeat.worker.__main__``). The match always runs; the
only setting is ``sources``, the sources the deployment turned on (``CLASSBEAT_SPOTDOWN_SOURCES``).
A source that is off, or spotdown not installed, is not a fallback: the song job ends
``analysis_unavailable`` (``spotdown.outage_policy``; the client says ``source_off`` or
``not_configured``).

The match sends metadata only and needs no ``cache_scope`` (YouTube analyses are global).
"""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class MatchConfig:
    sources: frozenset[str] = frozenset()


_config = MatchConfig()


def configure(config: MatchConfig | None) -> None:
    """Set the setting (None: the defaults)."""
    global _config
    _config = config or MatchConfig()


def current() -> MatchConfig:
    return _config
