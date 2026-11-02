"""The settings both processes give ``swedebeat_match`` (ADR 0016 sections 2 and 5).

The web app (``admit``: the environment's switch) and the worker (the switch again, the key ring
and the pacing) each call ``configure`` at start (``classbeat.web``, ``classbeat.worker.__main__``).
Without it the defaults apply and the feature is **off**: ``admit`` refuses every match with
``feature_off`` and a queued match ends ``feature_off`` without a request.
"""

from dataclasses import dataclass
from typing import Final

from classbeat.config import Settings
from classbeat.crypto.keks import KeyRing

# The pause between two catalogue requests of one job (one request in flight, ADR 0016 section 5).
DEFAULT_PACE_SECONDS: Final = 0.5


@dataclass(frozen=True, slots=True)
class MatchConfig:
    enabled: bool = False  # CLASSBEAT_SWEDEBEAT_ENABLED, the environment's kill switch
    keyring: KeyRing | None = None  # the worker's (to open the Swedebeat connection)
    pace_seconds: float = DEFAULT_PACE_SECONDS

    @classmethod
    def from_settings(cls, settings: Settings, keyring: KeyRing | None = None) -> "MatchConfig":
        return cls(enabled=settings.swedebeat_enabled, keyring=keyring)


_config = MatchConfig()


def configure(config: MatchConfig | None) -> None:
    """Set the settings (None: the defaults, off)."""
    global _config
    _config = config or MatchConfig()


def current() -> MatchConfig:
    return _config
