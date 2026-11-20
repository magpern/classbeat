"""The ``spotdown_service_key`` secret file, its holder and "available" (ADR 0040 section 10).
Pure: no I/O.

One shared service key, the value spotdown reads from its own ``SERVICE_KEY_FILE``. It is a
Docker secret file only (``/run/secrets/spotdown_service_key``, read by ``classbeat.config``),
never an environment value, mounted on the **worker only** (every spotdown call is a job step).
An empty file means no key. A non-empty value must be 32 to 256 printable ASCII characters without
whitespace (spotdown refuses a key shorter than 32; it goes into ``Authorization: Bearer``);
otherwise the start is refused, and the message names the file, never the value.

**The key never leaves the egress builder.** ``ServiceKey`` holds it with a constant ``repr`` and
``str``; only ``classbeat.egress_spotdown`` reads it (``bearer``), to set the header on a request
it has already checked against the one origin. It is never in a URL, a log line, an exception or
anything sent to a model.
"""

from typing import Final, NoReturn, Protocol

from pydantic import SecretStr

KEY_MIN: Final = 32
KEY_MAX: Final = 256
FILE_NAME: Final = "spotdown_service_key"
REDACTED: Final = "ServiceKey(****)"


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


class ServiceKey:
    """The checked key. ``repr``, ``str`` and ``format`` are a constant; it is not picklable and
    compares by identity only, so nothing prints, copies or compares the value by accident."""

    __slots__ = ("_value",)

    def __init__(self, value: SecretStr) -> None:
        checked = check_key(value)
        if checked is None:
            raise ValueError(f"{FILE_NAME} is empty")
        self._value = checked

    def bearer(self) -> str:
        """``Bearer <key>``, for ``classbeat.egress_spotdown`` only (the guard refuses others)."""
        return f"Bearer {self._value.get_secret_value()}"

    def __repr__(self) -> str:
        return REDACTED

    __str__ = __repr__

    def __format__(self, spec: str) -> str:
        return REDACTED

    def __reduce__(self) -> NoReturn:
        raise TypeError("a ServiceKey is not picklable")


class SpotdownSettings(Protocol):
    """What "available" reads of ``classbeat.config.Settings``."""

    @property
    def spotdown_url(self) -> str | None: ...

    @property
    def spotdown_service_key(self) -> SecretStr | None: ...


def spotdown_available(settings: SpotdownSettings) -> bool:
    """spotdown can be reached from this process: the URL **and** the key are set (both already
    checked at start). With either missing, nothing calls spotdown."""
    key = settings.spotdown_service_key
    return bool(settings.spotdown_url) and key is not None and bool(key.get_secret_value())
