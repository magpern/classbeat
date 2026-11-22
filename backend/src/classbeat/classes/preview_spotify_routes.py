"""``/api/classes/{id}/preview/spotify/...``: the Spotify Connect audition (ADR 0042 sections 7.2
and 7.3; the rules are ``classbeat.classes.preview_spotify``'s). Web only.

| Route | Does |
|---|---|
| ``POST .../audition`` ``{piece_index, position_ms}`` | plays that song of the current draft |
| ``POST .../pause`` | pauses, only while a song of this class plays |
| ``GET .../state`` | the observed state (section 7.3) |
| ``GET .../devices`` | the user's devices by name and type, and the one a play would use |

Signed in; with ``CLASSBEAT_SONG_PICKING`` off every route is 404 ``feature_off``. The ``POST``s
are CSRF-checked by the middleware. The audition body refuses any other field (a ``track_id``,
``uri`` or ``device_id`` is 422) and a non-integer. Another user's class, an unknown id and a
malformed one are the same 404 ``not_found``. Errors carry a reason code only (a 429 also
``retry_after`` and ``Retry-After``). Every answer is ``Cache-Control: no-store`` and carries only
the observed state and the proxy's own round trip to Spotify (``round_trip_ms``): never a token,
a device id or anything else of Spotify's.
"""

from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from classbeat import auth
from classbeat.classes import preview_spotify
from classbeat.classes.preview_spotify import Deps, Refusal, State
from classbeat.context import UserContext
from classbeat.spotify.player_http import SpotifyPlayerHttp
from classbeat.spotify.player_policy import Device
from classbeat.spotify.player_state_model import DeviceView
from classbeat.spotify.routes import spotify_http

NO_STORE = {"Cache-Control": "no-store"}
PIECE_MAX = 199  # a draft has at most 200 songs (``revision_spotdown_model.MAX_TRACKS``)
POSITION_MAX = 7_200_000  # a song is at most two hours (``song_list_model.Song.length_sec``)


async def require_song_picking(request: Request) -> None:
    if not request.app.state.settings.song_picking:
        raise HTTPException(status_code=404, detail={"reason": "feature_off"})


router = APIRouter(
    prefix="/api/classes",
    tags=["classes"],
    dependencies=[Depends(require_song_picking)],
)


class AuditionIn(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    piece_index: int = Field(ge=0, le=PIECE_MAX)
    position_ms: int = Field(ge=0, le=POSITION_MAX)


def _deps(request: Request) -> Deps:
    state = request.app.state
    player = getattr(state, "spotify_player_http", None)
    return Deps(
        state.session_factory,
        state.keyring,
        spotify_http(request),
        player if isinstance(player, SpotifyPlayerHttp) else SpotifyPlayerHttp(),
        state.settings.spotify_refresh_margin_seconds,
    )


def _uuid(raw: str) -> UUID | None:
    try:
        return UUID(raw)
    except ValueError:
        return None


def _answer(body: dict[str, Any]) -> JSONResponse:
    return JSONResponse(body, headers=NO_STORE)


def _refuse(refusal: Refusal) -> JSONResponse:
    detail: dict[str, Any] = {"reason": refusal.reason}
    headers = dict(NO_STORE)
    if refusal.status == 429 and refusal.retry_after is not None:
        detail["retry_after"] = refusal.retry_after
        headers["Retry-After"] = str(refusal.retry_after)
    if refusal.reason == "preview_not_granted":
        detail["action"] = "enable_listening_preview"
    return JSONResponse({"detail": detail}, status_code=refusal.status, headers=headers)


def _device(device: Device | DeviceView | None) -> dict[str, Any] | None:
    """A device by its name and type only: its id never leaves the backend."""
    return None if device is None else {"name": device.name, "type": device.type}


def _state(found: State) -> dict[str, Any]:
    seen = found.observed
    return {
        "status": found.status,
        "track_id": None if seen is None else seen.track_id,
        "pieces": found.pieces,
        "progress_ms": None if seen is None else seen.progress_ms,
        "is_playing": False if seen is None else seen.is_playing,
        "timestamp": None if seen is None else seen.timestamp,
        "device": None if seen is None else _device(seen.device),
        "round_trip_ms": found.round_trip_ms,
    }


_NOT_FOUND = Refusal("not_found", 404)


@router.post("/{class_id}/preview/spotify/audition", responses={404: {}, 409: {}, 422: {}})
async def audition(
    class_id: str,
    body: AuditionIn,
    request: Request,
    ctx: Annotated[UserContext, Depends(auth.require_user)],
) -> JSONResponse:
    """Play the current draft's song ``piece_index`` from ``position_ms`` on the user's device."""
    wanted = _uuid(class_id)
    if wanted is None:
        return _refuse(_NOT_FOUND)
    done = await preview_spotify.audition(
        _deps(request), ctx, wanted, body.piece_index, body.position_ms
    )
    if isinstance(done, Refusal):
        return _refuse(done)
    return _answer(
        {
            "status": "starting",
            "piece_index": done.piece_index,
            "track_id": done.track_id,
            "position_ms": done.position_ms,
            "device": _device(done.device),
            "round_trip_ms": done.round_trip_ms,
        }
    )


@router.post("/{class_id}/preview/spotify/pause", responses={404: {}, 409: {}})
async def pause(
    class_id: str, request: Request, ctx: Annotated[UserContext, Depends(auth.require_user)]
) -> JSONResponse:
    wanted = _uuid(class_id)
    if wanted is None:
        return _refuse(_NOT_FOUND)
    done = await preview_spotify.pause(_deps(request), ctx, wanted)
    if isinstance(done, Refusal):
        return _refuse(done)
    found, sent = done
    return _answer({**_state(found), "paused": sent})


@router.get("/{class_id}/preview/spotify/state", responses={404: {}, 409: {}})
async def state(
    class_id: str, request: Request, ctx: Annotated[UserContext, Depends(auth.require_user)]
) -> JSONResponse:
    wanted = _uuid(class_id)
    if wanted is None:
        return _refuse(_NOT_FOUND)
    done = await preview_spotify.state(_deps(request), ctx, wanted)
    return _refuse(done) if isinstance(done, Refusal) else _answer(_state(done))


@router.get("/{class_id}/preview/spotify/devices", responses={404: {}, 409: {}})
async def devices(
    class_id: str, request: Request, ctx: Annotated[UserContext, Depends(auth.require_user)]
) -> JSONResponse:
    wanted = _uuid(class_id)
    if wanted is None:
        return _refuse(_NOT_FOUND)
    done = await preview_spotify.devices(_deps(request), ctx, wanted)
    if isinstance(done, Refusal):
        return _refuse(done)
    return _answer(
        {
            "status": "ok" if done.devices else "no_device",
            "devices": [{**(_device(d) or {}), "is_active": d.is_active} for d in done.devices],
            "target": _device(done.target),
            "round_trip_ms": done.round_trip_ms,
        }
    )
