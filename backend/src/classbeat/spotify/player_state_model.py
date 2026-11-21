"""The observed playback state of the listening preview (ADR 0042 section 7.3). Pure: no I/O.

``observed`` reads a ``GET /v1/me/player`` answer (``None`` for its 204, nothing playing) into
only what the editor needs: the track id (validated, else none), ``progress_ms``, ``is_playing``,
Spotify's ``timestamp`` and the device's name and type. Every URL, URI, title and image of the
answer is dropped (ADR 0009 section 10).

``status_of`` classifies it against the class's current draft (the server's own track list):

| Observed | ``status`` |
|---|---|
| nothing playing (204, or no device in the answer) | ``idle`` |
| an item that is not a track of the class (another song, an episode, an ad, none) | ``external`` |
| a track of the class, ``is_playing`` false | ``paused`` |
| a track of the class, ``is_playing`` true | ``playing`` |

For ``external`` the track id is not passed on: the proxy reports what the user's Spotify plays
only when it is a song of this class (it is not a general remote control or listening history).
"""

from dataclasses import dataclass
from typing import Any, Final, Literal

from classbeat.spotify.player_policy import TRACK_ID, clean_name, clean_type

Status = Literal["idle", "external", "paused", "playing"]
PROGRESS_MAX: Final = 24 * 3600 * 1000
TIMESTAMP_MAX: Final = 2**53  # milliseconds since the epoch; anything larger is not one


@dataclass(frozen=True, slots=True)
class DeviceView:
    name: str
    type: str


@dataclass(frozen=True, slots=True)
class Observed:
    track_id: str | None  # a valid track id, else None (an episode, an ad, a local file)
    progress_ms: int | None
    is_playing: bool
    timestamp: int | None
    device: DeviceView | None


def _int(value: object, maximum: int) -> int | None:
    if isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= maximum:
        return value
    return None


def observed(answer: object) -> Observed | None:
    """None for nothing playing (a 204, or an answer without a device object)."""
    if not isinstance(answer, dict):
        return None
    data: dict[str, Any] = answer  # pyright: ignore[reportUnknownVariableType]
    device = data.get("device")
    if not isinstance(device, dict):
        return None
    item = data.get("item")
    track_id: str | None = None
    if (
        isinstance(item, dict)
        and data.get("currently_playing_type", "track") == "track"
        and item.get("type", "track") == "track"  # pyright: ignore[reportUnknownMemberType]
        and item.get("is_local") is not True  # pyright: ignore[reportUnknownMemberType]
    ):
        found = item.get("id")  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
        track_id = found if isinstance(found, str) and TRACK_ID.fullmatch(found) else None
    return Observed(
        track_id,
        _int(data.get("progress_ms"), PROGRESS_MAX),
        data.get("is_playing") is True,
        _int(data.get("timestamp"), TIMESTAMP_MAX),
        DeviceView(clean_name(device.get("name")), clean_type(device.get("type"))),  # pyright: ignore[reportUnknownMemberType, reportUnknownArgumentType]
    )


def status_of(seen: Observed | None, class_tracks: tuple[str, ...]) -> Status:
    if seen is None:
        return "idle"
    if seen.track_id is None or seen.track_id not in class_tracks:
        return "external"
    return "playing" if seen.is_playing else "paused"


def pieces_of(track_id: str | None, class_tracks: tuple[str, ...]) -> list[int]:
    """Every 0-based piece index of the class that plays ``track_id`` (a song can be in a class
    more than once)."""
    if track_id is None:
        return []
    return [i for i, t in enumerate(class_tracks) if t == track_id]
