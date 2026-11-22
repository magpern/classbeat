"""The Spotify Connect audition of a class (ADR 0042 sections 7.2 and 7.3): audition, pause, state
and devices, for the routes of ``classbeat.classes.preview_spotify_routes``. Web only.

**The gate**, in this order, before any Spotify request:

1. the class is the user's (``preview_tracks.load``): another user's or an unknown class is
   ``not_found``;
2. the user's Spotify connection is ``connected`` (``not_connected`` / ``needs_reconnect``) and
   its stored grant holds both preview scopes (``flow.preview_granted``): else
   ``preview_not_granted``, the "Enable listening preview" refusal;
3. for audition, pause and state, a current draft with its song list (``no_draft``).

**What can be played**: only ``spotify:track:<id>`` of the draft's song at ``piece_index``, from
``position_ms`` in ``[0, length)``, on a device of the server's own ``GET /v1/me/player/devices``
answer (``player_policy``). The browser sends neither a track nor a device. Pause is sent only
while the user's Spotify plays a song of this class; otherwise nothing is sent and the observed
state is answered. So these endpoints are not a general remote control.

Every request goes through ``player_access.player_call`` (the web's player mode of
``spotify_call``: no ``record_result``; a refresh only inside the margin; a 401 flips only after a
refresh and a repeat). Spotify's answers map to reasons: 403 ``premium_or_allowlist`` (Premium or
the app's development-mode allowlist), 404 on a command ``no_device``, 429 ``rate_limited`` with
its ``Retry-After``, 5xx ``upstream_unavailable``, anything else ``invalid_response``. No answer
carries a token, a device id, a URL or anything of Spotify's but the observed state.
"""

from dataclasses import dataclass
from typing import Any
from uuid import UUID

from classbeat import connections
from classbeat.classes import preview_tracks
from classbeat.classes.preview_tracks import DraftTracks
from classbeat.connections import ConnectionFailure
from classbeat.context import UserContext
from classbeat.egress import OutboundBlocked
from classbeat.spotify.flow import preview_granted
from classbeat.spotify.http import SpotifyHttp
from classbeat.spotify.player_access import Send, player_call
from classbeat.spotify.player_http import PlayerAnswer, SpotifyPlayerHttp
from classbeat.spotify.player_policy import (
    DEVICES,
    PAUSE,
    PLAY,
    STATE,
    Device,
    choose_device,
    devices_of,
    position_refusal,
    track_uri,
)
from classbeat.spotify.player_state_model import Observed, observed, pieces_of, status_of


@dataclass(frozen=True, slots=True)
class Deps:
    factory: Any
    ring: Any
    http: SpotifyHttp
    player: SpotifyPlayerHttp
    margin_seconds: float


@dataclass(frozen=True, slots=True)
class Refusal:
    reason: str
    status: int
    retry_after: int | None = None


@dataclass(frozen=True, slots=True)
class State:
    status: str
    observed: Observed | None
    pieces: list[int]
    round_trip_ms: int


@dataclass(frozen=True, slots=True)
class Audition:
    piece_index: int
    track_id: str
    position_ms: int
    device: Device
    round_trip_ms: int


@dataclass(frozen=True, slots=True)
class Devices:
    devices: list[Device]
    target: Device | None
    round_trip_ms: int


_FAILURES = {
    "not_connected": ("not_connected", 409),
    "invalid_grant": ("needs_reconnect", 409),
    "app_misconfigured": ("needs_reconnect", 409),
    "decrypt_failed": ("needs_reconnect", 409),
    "rate_limited": ("rate_limited", 429),
    "quota_exhausted": ("rate_limited", 429),
    "upstream_unavailable": ("upstream_unavailable", 503),
    "key_unavailable": ("upstream_unavailable", 503),
}


def _failure(failure: ConnectionFailure) -> Refusal:
    reason, status = _FAILURES.get(failure.category, ("invalid_response", 502))
    return Refusal(reason, status, failure.retry_after if status == 429 else None)


def _refusal_of(answer: PlayerAnswer) -> Refusal | None:
    """None for a 2xx; Spotify's refusal as a reason otherwise (module docstring)."""
    if 200 <= answer.status < 300:
        return None
    if answer.status == 403:
        return Refusal("premium_or_allowlist", 409)
    if answer.status == 404:
        return Refusal("no_device", 409)
    if answer.status == 429:
        return Refusal("rate_limited", 429, answer.retry_after)
    if 500 <= answer.status <= 599:
        return Refusal("upstream_unavailable", 503)
    return Refusal("invalid_response", 502)


async def _send(deps: Deps, ctx: UserContext, send: Send) -> PlayerAnswer | Refusal:
    try:
        answer = await player_call(
            deps.factory, deps.ring, ctx, send, http=deps.http, margin_seconds=deps.margin_seconds
        )
    except ConnectionFailure as failure:
        return _failure(failure)
    except OutboundBlocked:
        return Refusal("outbound_blocked", 502)
    refused = _refusal_of(answer)
    return answer if refused is None else refused


async def _gate(deps: Deps, ctx: UserContext, class_id: UUID) -> DraftTracks | Refusal | None:
    """The gate's steps 1 and 2; None when they pass but the class has no current draft."""
    found = await preview_tracks.load(deps.factory, ctx, class_id)
    if found == "not_found":
        return Refusal("not_found", 404)
    connection = await connections.spotify_status(deps.factory, ctx)
    if connection.status != "connected":
        return Refusal(connection.status, 409)
    if not preview_granted(connection.granted_scopes):
        return Refusal("preview_not_granted", 409)
    return None if isinstance(found, str) else found


async def _drafted(deps: Deps, ctx: UserContext, class_id: UUID) -> DraftTracks | Refusal:
    """The whole gate: steps 1 to 3."""
    gated = await _gate(deps, ctx, class_id)
    return Refusal("no_draft", 409) if gated is None else gated


async def _state(deps: Deps, ctx: UserContext, tracks: DraftTracks) -> State | Refusal:
    answer = await _send(deps, ctx, lambda token: deps.player.send(STATE, token))
    if isinstance(answer, Refusal):
        return answer
    seen = observed(answer.data) if answer.status == 200 else None
    status = status_of(seen, tracks.track_ids)
    if status == "external" and seen is not None:  # never another item's id
        seen = Observed(None, seen.progress_ms, seen.is_playing, seen.timestamp, seen.device)
    pieces = pieces_of(seen.track_id if seen else None, tracks.track_ids)
    return State(status, seen, pieces, answer.round_trip_ms)


async def state(deps: Deps, ctx: UserContext, class_id: UUID) -> State | Refusal:
    gated = await _drafted(deps, ctx, class_id)
    if isinstance(gated, Refusal):
        return gated
    return await _state(deps, ctx, gated)


async def _devices(deps: Deps, ctx: UserContext) -> Devices | Refusal:
    answer = await _send(deps, ctx, lambda token: deps.player.send(DEVICES, token))
    if isinstance(answer, Refusal):
        return answer
    found = devices_of(answer.data)
    return Devices(found, choose_device(found), answer.round_trip_ms)


async def devices(deps: Deps, ctx: UserContext, class_id: UUID) -> Devices | Refusal:
    gated = await _gate(deps, ctx, class_id)
    if isinstance(gated, Refusal):
        return gated
    return await _devices(deps, ctx)


async def audition(
    deps: Deps, ctx: UserContext, class_id: UUID, piece_index: int, position_ms: int
) -> Audition | Refusal:
    gated = await _drafted(deps, ctx, class_id)
    if isinstance(gated, Refusal):
        return gated
    if not 0 <= piece_index < len(gated.tracks):
        return Refusal("piece_out_of_range", 422)
    track = gated.tracks[piece_index]
    uri = track_uri(track.track_id)
    if uri is None:
        return Refusal("no_draft", 409)  # a stored id that is not a track id: never sent
    if position_refusal(position_ms, track.length_ms) is not None:
        return Refusal("position_out_of_range", 422)
    found = await _devices(deps, ctx)
    if isinstance(found, Refusal):
        return found
    if found.target is None:
        return Refusal("no_device", 409)
    device = found.target
    body = {"uris": [uri], "position_ms": position_ms}
    answer = await _send(
        deps, ctx, lambda token: deps.player.send(PLAY, token, device_id=device.id, body=body)
    )
    if isinstance(answer, Refusal):
        return answer
    return Audition(piece_index, track.track_id, position_ms, device, answer.round_trip_ms)


async def pause(deps: Deps, ctx: UserContext, class_id: UUID) -> tuple[State, bool] | Refusal:
    """The state and whether a pause was sent (only while a song of this class plays)."""
    gated = await _drafted(deps, ctx, class_id)
    if isinstance(gated, Refusal):
        return gated
    now = await _state(deps, ctx, gated)
    if isinstance(now, Refusal) or now.status != "playing":
        return now if isinstance(now, Refusal) else (now, False)
    answer = await _send(deps, ctx, lambda token: deps.player.send(PAUSE, token))
    if isinstance(answer, Refusal):
        return answer
    return State("paused", now.observed, now.pieces, answer.round_trip_ms), True
