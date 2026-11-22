"""The listening preview's pure rules (ADR 0042 sections 7.2 and 7.3, #1395): the exact player
paths, the rebuilt track URI, the position bound, the device choice and the observed state."""

import pytest

from classbeat.spotify import player_policy as policy
from classbeat.spotify.player_policy import (
    DEVICES,
    PAUSE,
    PLAY,
    PLAYER_CALLS,
    STATE,
    Device,
    allowed,
    choose_device,
    devices_of,
    position_refusal,
    track_uri,
)
from classbeat.spotify.player_state_model import Observed, observed, pieces_of, status_of

TID = "4uLU6hMCjMI75M1A2tKUQC"
OTHER = "7GhIk7Il098yCjg4BQjzvb"
NONE: frozenset[str] = frozenset()


def test_the_four_calls_are_exactly_the_adr_paths() -> None:
    assert {(c.method, c.path) for c in PLAYER_CALLS} == {
        ("PUT", "/v1/me/player/play"),
        ("PUT", "/v1/me/player/pause"),
        ("GET", "/v1/me/player"),
        ("GET", "/v1/me/player/devices"),
    }


@pytest.mark.parametrize("call", [PLAY, PAUSE, STATE, DEVICES])
def test_each_call_is_allowed_as_it_is(call: policy.PlayerCall) -> None:
    assert allowed(call.method, "https", "api.spotify.com", call.path, NONE)


@pytest.mark.parametrize(
    ("method", "scheme", "host", "path", "keys"),
    [
        ("POST", "https", "api.spotify.com", "/v1/me/player/next", NONE),
        ("POST", "https", "api.spotify.com", "/v1/me/player/previous", NONE),
        ("PUT", "https", "api.spotify.com", "/v1/me/player/seek", frozenset({"position_ms"})),
        ("PUT", "https", "api.spotify.com", "/v1/me/player/volume", NONE),
        ("PUT", "https", "api.spotify.com", "/v1/me/player/shuffle", NONE),
        ("PUT", "https", "api.spotify.com", "/v1/me/player/repeat", NONE),
        ("POST", "https", "api.spotify.com", "/v1/me/player/queue", NONE),
        ("GET", "https", "api.spotify.com", "/v1/me/player/queue", NONE),
        ("GET", "https", "api.spotify.com", "/v1/me/player/currently-playing", NONE),
        ("GET", "https", "api.spotify.com", "/v1/me/player/recently-played", NONE),
        ("PUT", "https", "api.spotify.com", "/v1/me/player", NONE),  # a transfer
        ("GET", "https", "api.spotify.com", "/v1/me/player/play", NONE),  # another method
        ("POST", "https", "api.spotify.com", "/v1/me/player/play", NONE),
        ("DELETE", "https", "api.spotify.com", "/v1/me/player/pause", NONE),
        ("PUT", "https", "api.spotify.com", "/v1/me/player/play/", NONE),  # a trailing slash
        ("PUT", "https", "api.spotify.com", "/v1/me/Player/play", NONE),
        ("GET", "https", "api.spotify.com", "/v1/me/player/devices/x", NONE),
        ("GET", "https", "api.spotify.com", "/v1/me", NONE),
        ("PUT", "http", "api.spotify.com", "/v1/me/player/play", NONE),
        ("PUT", "https", "accounts.spotify.com", "/v1/me/player/play", NONE),
        ("PUT", "https", "api.spotify.com.evil.example", "/v1/me/player/play", NONE),
        ("PUT", "https", "api.spotify.com", "/v1/me/player/pause", frozenset({"device_id"})),
        ("PUT", "https", "api.spotify.com", "/v1/me/player/play", frozenset({"context_uri"})),
        ("GET", "https", "api.spotify.com", "/v1/me/player", frozenset({"market"})),
    ],
)
def test_anything_else_is_refused(
    method: str, scheme: str, host: str, path: str, keys: frozenset[str]
) -> None:
    assert not allowed(method, scheme, host, path, keys)


def test_a_play_may_name_its_device_and_nothing_else() -> None:
    assert allowed("PUT", "https", "api.spotify.com", PLAY.path, frozenset({"device_id"}))
    assert allowed("put", "https", "api.spotify.com", PLAY.path, frozenset())


@pytest.mark.parametrize(
    "value",
    [
        "4uLU6hMCjMI75M1A2tKUQ",  # 21
        "4uLU6hMCjMI75M1A2tKUQCC",  # 23
        "spotify:track:4uLU6hMCjMI75M1A2tKUQC",
        "4uLU6hMCjMI75M1A2tKU-C",
        "4uLU6hMCjMI75M1A2tKUQ\n",
        "../../v1/me/player/next",
        "",
        None,
        22,
    ],
)
def test_only_a_valid_track_id_becomes_a_uri(value: object) -> None:
    assert track_uri(value) is None


def test_the_uri_is_rebuilt_from_the_id() -> None:
    assert track_uri(TID) == f"spotify:track:{TID}"


@pytest.mark.parametrize(
    ("position", "length", "refused"),
    [
        (0, 1000, False),
        (999, 1000, False),
        (1000, 1000, True),
        (-1, 1000, True),
        (5, 0, True),
    ],
)
def test_the_position_is_inside_the_track(position: int, length: int, refused: bool) -> None:
    assert (position_refusal(position, length) is not None) is refused


def entry(device_id: object, **more: object) -> dict[str, object]:
    return {"id": device_id, "name": "Laptop", "type": "Computer", "is_active": False, **more}


def test_the_active_device_is_chosen_else_the_first_and_restricted_ones_never() -> None:
    answer = {
        "devices": [
            entry("aa11", is_restricted=True, is_active=True),
            entry("bb22"),
            entry("cc33", is_active=True),
        ]
    }
    found = devices_of(answer)
    assert [d.id for d in found] == ["bb22", "cc33"]
    assert choose_device(found) == found[1]
    assert choose_device(found[:1]) == found[0]
    assert choose_device([]) is None


@pytest.mark.parametrize("device_id", [None, "", 7, "a b", "x" * 129, "id?x=1", "id/../x"])
def test_a_device_of_an_unexpected_shape_is_skipped(device_id: object) -> None:
    assert devices_of({"devices": [entry(device_id)]}) == []


@pytest.mark.parametrize("answer", [None, [], {"devices": None}, {"devices": "x"}, "x"])
def test_an_answer_without_devices_has_none(answer: object) -> None:
    assert devices_of(answer) == []


def test_a_device_is_cleaned_and_its_repr_hides_its_id() -> None:
    (found,) = devices_of(
        {"devices": [entry("dd44", name="Bike\x00 room" + "x" * 200, type="<b>")]}
    )
    assert found.name.startswith("Bike room")
    assert len(found.name) == 100
    assert found.type == "Unknown"
    assert "dd44" not in repr(found)
    assert isinstance(found, Device)


def state(**more: object) -> dict[str, object]:
    return {
        "device": {"id": "dd44", "name": "Laptop", "type": "Computer"},
        "progress_ms": 1234,
        "is_playing": True,
        "timestamp": 1_700_000_000_000,
        "currently_playing_type": "track",
        "item": {"id": TID, "type": "track", "uri": f"spotify:track:{TID}", "href": "https://x"},
        **more,
    }


TRACKS = (OTHER, TID, TID)


def test_the_state_table() -> None:
    assert status_of(observed(None), TRACKS) == "idle"
    assert status_of(observed({"is_playing": True}), TRACKS) == "idle"  # no device
    assert status_of(observed(state()), TRACKS) == "playing"
    assert status_of(observed(state(is_playing=False)), TRACKS) == "paused"
    other = {"id": "0000000000000000000000", "type": "track"}
    assert status_of(observed(state(item=other)), TRACKS) == "external"
    episode = {"id": TID, "type": "episode"}
    assert status_of(observed(state(item=episode)), TRACKS) == "external"
    assert status_of(observed(state(currently_playing_type="ad", item=None)), TRACKS) == "external"
    local = {"id": TID, "type": "track", "is_local": True}
    assert status_of(observed(state(item=local)), TRACKS) == "external"


def test_only_the_observed_values_are_kept() -> None:
    seen = observed(state())
    assert seen == Observed(TID, 1234, True, 1_700_000_000_000, seen.device if seen else None)
    assert seen is not None
    assert seen.device is not None
    assert (seen.device.name, seen.device.type) == ("Laptop", "Computer")
    assert not hasattr(seen.device, "id")


@pytest.mark.parametrize("value", [-1, True, "12", 1.5, 10**12])
def test_a_progress_that_is_no_position_is_none(value: object) -> None:
    seen = observed(state(progress_ms=value))
    assert seen is not None
    assert seen.progress_ms is None


def test_a_track_in_the_class_twice_names_both_pieces() -> None:
    assert pieces_of(TID, TRACKS) == [1, 2]
    assert pieces_of(None, TRACKS) == []
    assert pieces_of("0000000000000000000000", TRACKS) == []
