"""The settings both processes give ``swedebeat_match`` (ADR 0016 sections 2 and 5; ADR 0035).

The web app (``admit``: the environment's switch) and the worker (the switch again, the key ring,
the gateway's URL and its client key) each call ``configure`` at start (``classbeat.web``,
``classbeat.worker.__main__``). Without it the defaults apply and the feature is **off**:
``admit`` refuses every match with ``feature_off`` and a queued match ends ``feature_off`` without
a request. Without the gateway's URL or key a queued match ends ``app_misconfigured`` without a
request (``key_policy.gateway_available``).
"""

from dataclasses import dataclass

from pydantic import SecretStr

from classbeat.config import Settings
from classbeat.crypto.keks import KeyRing
from classbeat.swedebeat_gateway.key_policy import gateway_available


@dataclass(frozen=True, slots=True)
class MatchConfig:
    enabled: bool = False  # CLASSBEAT_SWEDEBEAT_ENABLED, the environment's kill switch
    keyring: KeyRing | None = None  # the worker's (to open the Swedebeat connection)
    swedebeat_gateway_url: str | None = None  # the checked origin (ADR 0035 section 1)
    swedebeat_gateway_key: SecretStr | None = None  # X-API-Key (section 2), a SecretStr

    @property
    def available(self) -> bool:
        """The gateway's URL and its client key are both set."""
        return gateway_available(self)

    @classmethod
    def from_settings(cls, settings: Settings, keyring: KeyRing | None = None) -> "MatchConfig":
        return cls(
            enabled=settings.swedebeat_enabled,
            keyring=keyring,
            swedebeat_gateway_url=settings.swedebeat_gateway_url,
            swedebeat_gateway_key=settings.swedebeat_gateway_key,
        )


_config = MatchConfig()


def configure(config: MatchConfig | None) -> None:
    """Set the settings (None: the defaults, off)."""
    global _config
    _config = config or MatchConfig()


def current() -> MatchConfig:
    return _config
