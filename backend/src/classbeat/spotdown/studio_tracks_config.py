"""The worker's settings of studio tracks analysed by spotdown (ADR 0040 section 9, rollout step
2; SD-P5, #1302).

The worker calls ``configure`` at start (``classbeat.worker.__main__``). Without it the defaults
apply and the studio's tracks are analysed **locally, as before** (``mowl.music.analyze_track`` in
the sandboxed child). spotdown is the source of a track's map (``active_for``) only when all of
these hold:

- ``CLASSBEAT_SPOTDOWN_STUDIO`` is ``allowlist`` (default ``off``) and the job's owner is in
  ``CLASSBEAT_SPOTDOWN_STUDIO_OWNERS`` (``studio_owners_policy``). The default switch (every
  owner) waits for the studio gates of plan section 7 (owner decision #1298) and is not offered;
- ``upload`` is in ``CLASSBEAT_SPOTDOWN_SOURCES`` (classbeat relays the bytes it downloaded);
- spotdown is installed in this process (``CLASSBEAT_SPOTDOWN_URL`` and its key file:
  ``egress_spotdown.spotdown_installed``);
- the ``cache_scope`` key is there (derived from ``app_secret_key``, ``scope_key``).

Otherwise nothing is called and nothing is written that a local run does not write. On any
spotdown failure the local path analyses that track (``design.track_spotdown``).
"""

from dataclasses import dataclass, field
from typing import Literal
from uuid import UUID

from classbeat import egress_spotdown

Mode = Literal["off", "allowlist"]


@dataclass(frozen=True, slots=True)
class TracksConfig:
    mode: Mode = "off"
    owners: frozenset[UUID] = frozenset()
    sources: frozenset[str] = frozenset()
    scope_key: bytes | None = field(default=None, repr=False)

    def active_for(self, owner_id: UUID) -> bool:
        return (
            self.mode == "allowlist"
            and owner_id in self.owners
            and "upload" in self.sources
            and self.scope_key is not None
            and egress_spotdown.spotdown_installed()
        )


_config = TracksConfig()


def configure(config: TracksConfig | None) -> None:
    """Set the settings (None: the defaults, off)."""
    global _config
    _config = config or TracksConfig()


def current() -> TracksConfig:
    return _config
