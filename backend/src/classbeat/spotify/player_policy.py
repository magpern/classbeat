"""What the listening-preview proxy may send to Spotify (ADR 0042 section 7.2, amending ADR 0009
section 10). Pure: no I/O.

- **The paths** (``PLAYER_CALLS``): exactly ``PUT /v1/me/player/play``, ``PUT
  /v1/me/player/pause``, ``GET /v1/me/player`` and ``GET /v1/me/player/devices`` on
  ``api.spotify.com``. ``allowed`` is the runtime check ``classbeat.spotify.player_http`` runs on
  every built request (method, scheme, host, path and query keys), and
  ``tests/test_spotify_player_guard.py`` checks statically that no other module names a player
  path. No ``next``, ``previous``, ``seek``, ``queue``, ``volume``, ``shuffle``, ``repeat`` or
  transfer (``PUT /v1/me/player``): the proxy is not a general remote control.
- **The track** is never the browser's: ``track_uri`` rebuilds ``spotify:track:<id>`` from an id
  the server read from the class's current draft, after ``^[0-9A-Za-z]{22}$``.
- **The position** is in ``[0, length)`` of that track (``position_refusal``).
- **The device** is never the browser's: ``choose_device`` picks one from the server's own
  ``GET /v1/me/player/devices`` answer (the active one, else the first), skipping a restricted
  device (Spotify refuses commands to it) and any whose id is not of the expected shape.
"""

import re
from dataclasses import dataclass
from typing import Any, Final

API_HOST: Final = "api.spotify.com"  # ``classbeat.egress.SPOTIFY_API_HOST``; this module has no I/O


@dataclass(frozen=True, slots=True)
class PlayerCall:
    method: str
    path: str


PLAY: Final = PlayerCall("PUT", "/v1/me/player/play")
PAUSE: Final = PlayerCall("PUT", "/v1/me/player/pause")
STATE: Final = PlayerCall("GET", "/v1/me/player")
DEVICES: Final = PlayerCall("GET", "/v1/me/player/devices")
PLAYER_CALLS: Final = frozenset({PLAY, PAUSE, STATE, DEVICES})
# The one query key a call may carry: the device of a play, from the server's own devices list.
QUERY_KEYS: Final[dict[PlayerCall, frozenset[str]]] = {
    PLAY: frozenset({"device_id"}),
    PAUSE: frozenset(),
    STATE: frozenset(),
    DEVICES: frozenset(),
}

TRACK_ID: Final = re.compile(r"[0-9A-Za-z]{22}")
# Spotify documents a device id as a string; the ones seen are 40 hex characters. Anything not of
# this shape is never sent (an assumption to check at the owner's live run, ADR 0042 section 7.3).
DEVICE_ID: Final = re.compile(r"[0-9A-Za-z_-]{1,128}")
NAME_MAX: Final = 100
TYPE: Final = re.compile(r"[A-Za-z_]{1,32}")


def allowed(method: str, scheme: str, host: str, path: str, query_keys: frozenset[str]) -> bool:
    """Whether a built request is one of ``PLAYER_CALLS`` exactly: https, ``api.spotify.com``,
    the call's method and path, and only its query keys."""
    call = PlayerCall(method.upper(), path)
    return (
        call in PLAYER_CALLS
        and scheme == "https"
        and host == API_HOST
        and query_keys <= QUERY_KEYS[call]
    )


def track_uri(track_id: object) -> str | None:
    """``spotify:track:<id>`` rebuilt from a validated id, or None."""
    if not isinstance(track_id, str) or TRACK_ID.fullmatch(track_id) is None:
        return None
    return f"spotify:track:{track_id}"


def position_refusal(position_ms: int, length_ms: int) -> str | None:
    """``position_out_of_range`` unless ``0 <= position_ms < length_ms``."""
    if length_ms <= 0 or not 0 <= position_ms < length_ms:
        return "position_out_of_range"
    return None


@dataclass(frozen=True, slots=True)
class Device:
    """A device from the server's own devices list. ``id`` never leaves the backend."""

    id: str
    name: str
    type: str
    is_active: bool

    def __repr__(self) -> str:
        return f"Device(name={self.name!r}, type={self.type!r}, is_active={self.is_active})"


def clean_name(value: object) -> str:
    """A device name as shown: printable characters only, at most ``NAME_MAX``."""
    if not isinstance(value, str):
        return ""
    return "".join(c for c in value if c.isprintable())[:NAME_MAX]


def clean_type(value: object) -> str:
    return value if isinstance(value, str) and TYPE.fullmatch(value) else "Unknown"


def device_of(entry: object) -> Device | None:
    """One entry of a devices answer (or a state answer's ``device``), or None when its id is
    missing or not of the expected shape."""
    if not isinstance(entry, dict):
        return None
    data: dict[str, Any] = entry  # pyright: ignore[reportUnknownVariableType]
    device_id = data.get("id")
    if not isinstance(device_id, str) or DEVICE_ID.fullmatch(device_id) is None:
        return None
    return Device(
        device_id, clean_name(data.get("name")), clean_type(data.get("type")),
        data.get("is_active") is True,
    )  # fmt: skip


def devices_of(answer: object, limit: int = 20) -> list[Device]:
    """The usable devices of a ``GET /v1/me/player/devices`` answer: valid ids, not restricted,
    at most ``limit``."""
    entries = answer.get("devices") if isinstance(answer, dict) else None  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
    if not isinstance(entries, list):
        return []
    found: list[Device] = []
    for entry in entries:  # pyright: ignore[reportUnknownVariableType]
        device = device_of(entry)
        if device is not None and entry.get("is_restricted") is not True:  # pyright: ignore[reportUnknownMemberType]
            found.append(device)
        if len(found) >= limit:
            break
    return found


def choose_device(devices: list[Device]) -> Device | None:
    """The active device, else the first; None when there is none (``no_device``)."""
    for device in devices:
        if device.is_active:
            return device
    return devices[0] if devices else None
