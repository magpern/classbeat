"""The ``swedebeat_gateway_key`` secret file and "available" (ADR 0035 section 2). Pure: no I/O.

The client key is the value the gateway lists for classbeat in its ``CLIENT_API_KEYS``. It is a
Docker secret file only (``/run/secrets/swedebeat_gateway_key`` or the file that
``CLASSBEAT_SWEDEBEAT_GATEWAY_KEY_FILE`` names, read by ``classbeat.config``), never an environment
value, and it is mounted on web and worker only. An empty file means no key. A non-empty value
must be 16 to 256 printable ASCII characters without whitespace (it goes into ``X-API-Key``);
otherwise the start is refused, and the message names the file, never the value.
"""

from typing import Final, Protocol

from pydantic import SecretStr

KEY_MIN: Final = 16
KEY_MAX: Final = 256
FILE_NAME: Final = "swedebeat_gateway_key"


def check_key(value: SecretStr | None) -> SecretStr | None:
    """The setting's validator: ``None`` for a missing or empty file, else the checked value."""
    if value is None:
        return None
    key = value.get_secret_value()
    if not key.strip():
        return None  # the empty placeholder: no key on this server
    visible = all("\x21" <= c <= "\x7e" for c in key)
    if not (KEY_MIN <= len(key) <= KEY_MAX and visible):
        raise ValueError(
            f"{FILE_NAME} must be {KEY_MIN} to {KEY_MAX} printable ASCII characters without"
            " whitespace"
        )
    return value


class GatewaySettings(Protocol):
    """What "available" reads of ``classbeat.config.Settings``."""

    @property
    def swedebeat_gateway_url(self) -> str | None: ...

    @property
    def swedebeat_gateway_key(self) -> SecretStr | None: ...


def gateway_available(settings: GatewaySettings) -> bool:
    """SwedeBeat can be reached on this server: the gateway URL **and** the client key are set
    (both already checked at start). The flag, the admin flag and the user's preference are
    separate (ADR 0015 section 6); the account page's ``swedebeat.available`` uses this (SG-2)."""
    key = settings.swedebeat_gateway_key
    return bool(settings.swedebeat_gateway_url) and key is not None and bool(key.get_secret_value())
