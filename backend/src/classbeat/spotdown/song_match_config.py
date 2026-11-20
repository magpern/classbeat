"""The worker's settings of the Spotify song stage's spotdown match (ADR 0040 sections 2, 8 and 9;
SD-P6 stage A, #1303).

The worker calls ``configure`` at start (``classbeat.worker.__main__``). Without it the defaults
apply and a song pick runs **exactly as before** (MOWL's pool sections only, no spotdown call, no
row written). The match runs (``active_for``) only when all of these hold:

- ``CLASSBEAT_SPOTDOWN_SPOTIFY`` is ``allowlist`` (default ``off``) and the job's owner is in
  ``CLASSBEAT_SPOTDOWN_SPOTIFY_OWNERS`` (``owner_list_policy``). **No value turns it on for
  everyone**: the default switch waits for the Spotify gates of plan section 7 (owner decision
  #1298) and is not offered;
- ``youtube`` is in ``CLASSBEAT_SPOTDOWN_SOURCES`` (``POST /v2/match`` is the YouTube matcher);
- spotdown is installed in this process (``CLASSBEAT_SPOTDOWN_URL`` and its key file:
  ``egress_spotdown.spotdown_installed``).

The match sends metadata only and needs no ``cache_scope`` (YouTube analyses are global), so no
scope key is read here.
"""

from dataclasses import dataclass
from typing import Literal
from uuid import UUID

from classbeat import egress_spotdown

Mode = Literal["off", "allowlist"]


@dataclass(frozen=True, slots=True)
class MatchConfig:
    mode: Mode = "off"
    owners: frozenset[UUID] = frozenset()
    sources: frozenset[str] = frozenset()

    def active_for(self, owner_id: UUID) -> bool:
        return (
            self.mode == "allowlist"
            and owner_id in self.owners
            and "youtube" in self.sources
            and egress_spotdown.spotdown_installed()
        )


_config = MatchConfig()


def configure(config: MatchConfig | None) -> None:
    """Set the settings (None: the defaults, off)."""
    global _config
    _config = config or MatchConfig()


def current() -> MatchConfig:
    return _config
