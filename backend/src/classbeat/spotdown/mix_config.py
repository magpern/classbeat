"""The worker's settings of SwedeBeat mixes analysed by spotdown (ADR 0040 section 9, rollout step
2; SD-P7a, #1304).

The worker calls ``configure`` at start (``classbeat.worker.__main__``). Without it the defaults
apply and a mix is analysed **locally, as before** (``design.mixanalysis`` in the sandboxed
``mixchild``). spotdown is the source of a mix's map (``TracksConfig.active_for``, the same rule
as the studio's ``studio_tracks_config``) only when all of these hold:

- ``CLASSBEAT_SPOTDOWN_MIX`` is ``allowlist`` (default ``off``) and the job's owner is in
  ``CLASSBEAT_SPOTDOWN_MIX_OWNERS`` (``studio_owners_policy.canonical_mix``). The default switch
  (every owner) waits for the mix gates of plan section 7 (owner decision #1298) and is not
  offered;
- ``upload`` is in ``CLASSBEAT_SPOTDOWN_SOURCES`` (classbeat relays the bytes it fetched);
- spotdown is installed in this process (``egress_spotdown.spotdown_installed``);
- the ``cache_scope`` key is there (derived from ``app_secret_key``).

Otherwise nothing is called and nothing is written that a local run does not write. It is a
setting of its own: switching the studio's on never switches the mix's on.
"""

from classbeat.spotdown.studio_tracks_config import TracksConfig

_config = TracksConfig()


def configure(config: TracksConfig | None) -> None:
    """Set the settings (None: the defaults, off)."""
    global _config
    _config = config or TracksConfig()


def current() -> TracksConfig:
    return _config
