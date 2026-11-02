"""The settings both processes give the Swedebeat hand-off (ADR 0016 sections 2 and 7).

The web app (``admit``) and the worker (the switch again, and the key ring that opens MOWL)
each call ``configure`` at start. Without it the feature is off: ``admit`` refuses every verify
job with ``feature_off`` and a queued one ends ``feature_off`` without a request.
"""

from dataclasses import dataclass
from typing import Final

from classbeat.config import Settings
from classbeat.crypto.keks import KeyRing

# How long a hand-off row stays usable. The ADR requires an expiry and a lazy refusal; it does
# not name the window. One day is long enough to build the mix in the Swedebeat app and short
# enough that a forgotten row cannot be revived later. The table allows at most 7 days.
TTL_HOURS: Final = 24
# Presses of "I made the mix" / "Check again" (ADR 0016 section 7).
CHECKS_PER_HOUR: Final = 10
# The maintenance pass deletes rows this long after they expired.
PURGE_AFTER_DAYS: Final = 7


@dataclass(frozen=True, slots=True)
class HandoffConfig:
    enabled: bool = False  # CLASSBEAT_SWEDEBEAT_ENABLED
    keyring: KeyRing | None = None  # the worker's, to open the MOWL connection

    @classmethod
    def from_settings(cls, settings: Settings, keyring: KeyRing | None = None) -> "HandoffConfig":
        return cls(enabled=settings.swedebeat_enabled, keyring=keyring)


_config = HandoffConfig()


def configure(config: HandoffConfig | None) -> None:
    """Set the settings (None: the defaults, off)."""
    global _config
    _config = config or HandoffConfig()


def current() -> HandoffConfig:
    return _config
