"""The worker's settings of the studio tracks' and mixes' analysis by spotdown (ADR 0040 amendment
"spotdown is the only analysis path", item 2; #1353).

The worker calls ``configure`` at start (``classbeat.worker.__main__``). There is no switch and no
allowlist: spotdown is the only analysis path. It is **usable** (``refusal`` is ``None``) only when
all of these hold:

- spotdown is installed in this process (``CLASSBEAT_SPOTDOWN_URL`` and its key file:
  ``egress_spotdown.spotdown_installed``);
- ``upload`` is in ``CLASSBEAT_SPOTDOWN_SOURCES`` (classbeat relays the bytes it downloaded; the
  setting goes in #1355, until then a missing ``upload`` reads as not installed);
- the ``cache_scope`` key is there (derived from ``app_secret_key``, ``scope_key``).

Otherwise every analysis of a studio track or a mix ends ``analysis_unavailable`` (retried) before
anything is downloaded, fetched or counted; a cache hit needs none of this. The studio's tracks and
the mixes share this one object.
"""

from dataclasses import dataclass, field

from classbeat import egress_spotdown
from classbeat.spotdown.failure_policy import UnreachableReason


@dataclass(frozen=True, slots=True)
class TracksConfig:
    sources: frozenset[str] = frozenset()
    scope_key: bytes | None = field(default=None, repr=False)

    def refusal(self) -> UnreachableReason | None:
        """Why spotdown cannot be used (the ``Unreachable`` reason it stands for), else ``None``."""
        if self.scope_key is None or not egress_spotdown.spotdown_installed():
            return "not_configured"
        if "upload" not in self.sources:
            return "source_off"
        return None


_config = TracksConfig()


def configure(config: TracksConfig | None) -> None:
    """Set the settings (None: the defaults, not usable)."""
    global _config
    _config = config or TracksConfig()


def current() -> TracksConfig:
    return _config
