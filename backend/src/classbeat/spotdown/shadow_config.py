"""The worker's settings of shadow mode for studio tracks (ADR 0040 section 9, rollout step 1).

The worker calls ``configure`` at start (``classbeat.worker.__main__``). Without it the defaults
apply and shadow mode is **off**. It is **active** (``active``) only when all of these hold:

- ``CLASSBEAT_SPOTDOWN_SHADOW`` is true (default false);
- ``upload`` is in ``CLASSBEAT_SPOTDOWN_SOURCES`` (classbeat relays the bytes it downloaded);
- spotdown is installed in this process (``CLASSBEAT_SPOTDOWN_URL`` and its key file, read only by
  the worker's entry point: ``egress_spotdown.spotdown_installed``);
- the ``cache_scope`` key is there (derived from ``app_secret_key``, ``scope_key``).

Inactive, the studio's track analysis calls nothing and writes nothing more than before.
"""

from dataclasses import dataclass, field

from classbeat import egress_spotdown


@dataclass(frozen=True, slots=True)
class ShadowConfig:
    enabled: bool = False
    sources: frozenset[str] = frozenset()
    scope_key: bytes | None = field(default=None, repr=False)

    @property
    def active(self) -> bool:
        return (
            self.enabled
            and "upload" in self.sources
            and self.scope_key is not None
            and egress_spotdown.spotdown_installed()
        )


_config = ShadowConfig()


def configure(config: ShadowConfig | None) -> None:
    """Set the settings (None: the defaults, off)."""
    global _config
    _config = config or ShadowConfig()


def current() -> ShadowConfig:
    return _config
