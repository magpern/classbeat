"""The studio's settings both processes ``configure`` at start (ADR 0036 sections 2 and 10).

``StudioConfig.values`` is ``CLASSBEAT_SWEDEBEAT_STUDIO`` as ``config_policy`` read it. The
studio's song stage runs only with ``design`` in it **and** SwedeBeat on (the gateway's switch,
URL and key, ``swedebeat_match.config``, which both processes configure as well). Without
``configure`` the defaults apply: the studio is off, ``studio_songs`` is refused at admission
(``feature_off``) and a queued one ends ``feature_off`` before any call.
"""

from dataclasses import dataclass, field

from classbeat.config import Settings
from classbeat.swedebeat_studio.config_policy import BASS_SWAP, DESIGN, studio_values


@dataclass(frozen=True, slots=True)
class StudioConfig:
    values: frozenset[str] = field(default_factory=frozenset[str])

    @property
    def design(self) -> bool:
        """The studio itself (its song stage): ``design`` is on."""
        return DESIGN in self.values

    @property
    def bass_swap(self) -> bool:
        """The mix's bass swap (ADR 0036 amendment B8): ``bass_swap`` is on (off by default)."""
        return BASS_SWAP in self.values

    @classmethod
    def from_settings(cls, settings: Settings) -> "StudioConfig":
        return cls(values=studio_values(settings.swedebeat_studio))


_config = StudioConfig()


def configure(config: StudioConfig | None) -> None:
    """Set the settings (None: the defaults, off)."""
    global _config
    _config = config or StudioConfig()


def current() -> StudioConfig:
    return _config
