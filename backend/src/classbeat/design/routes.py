"""``GET /api/music/mowl-playlists``: the user's Spotify playlists as MOWL lists them, for the
new-class form's playlist choice (ADR 0006 section 3, D1). Web only.

Signed in. Opens the user's MOWL connection through ``classbeat.connections.use`` (a read:
``mowl.api.spotify_playlists``) and answers per playlist only its Spotify id (what the job input
names, ``music.spotify_playlist_id``) and its name: no track counts, MOWL ids, URLs or texts. An
entry whose id is not a Spotify playlist id is left out; names lose control characters and are
cut to 200 characters (they are the user's own playlist names, shown back to them).

A failure answers with its category only, as the connection routes do: ``not_connected``,
``needs_reconnect`` and ``decrypt_failed`` 409 (a 401 or 403 from MOWL flips the connection to
``needs_reconnect``), ``rate_limited`` 429 with ``Retry-After``, ``upstream_unavailable`` and
``key_unavailable`` 503, anything else 502.
"""

import re
from typing import Annotated, Any, Final

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from mowl import api as mowl_api
from pydantic import BaseModel

from classbeat import auth
from classbeat.agent.schemas import clean_text
from classbeat.connections import ConnectionFailure, Credentials, use
from classbeat.context import UserContext

router = APIRouter(prefix="/api/music", tags=["music"])

SPOTIFY_ID: Final = re.compile(r"^[0-9A-Za-z]{22}$")
MAX_NAME: Final = 200
MAX_PLAYLISTS: Final = 500
DEFAULT_RETRY_AFTER: Final = 60
_STATUS: Final = {
    "not_connected": 409,
    "needs_reconnect": 409,
    "decrypt_failed": 409,
    "rate_limited": 429,
    "upstream_unavailable": 503,
    "key_unavailable": 503,
}


class PlaylistOut(BaseModel):
    spotify_playlist_id: str
    name: str


class PlaylistsOut(BaseModel):
    playlists: list[PlaylistOut]


def _listed(answer: object) -> list[PlaylistOut]:
    out: list[PlaylistOut] = []
    entries: list[Any] = answer if isinstance(answer, list) else []  # pyright: ignore[reportUnknownVariableType]
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        playlist_id: object = entry.get("ID")  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
        name: object = entry.get("Name")  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
        if not isinstance(playlist_id, str) or not SPOTIFY_ID.fullmatch(playlist_id):
            continue
        out.append(
            PlaylistOut(
                spotify_playlist_id=playlist_id,
                name=clean_text(name if isinstance(name, str) else "")[:MAX_NAME],
            )
        )
        if len(out) >= MAX_PLAYLISTS:
            break
    return out


def _read(creds: Credentials) -> object:
    return mowl_api.spotify_playlists(creds)


@router.get(
    "/mowl-playlists",
    response_model=PlaylistsOut,
    responses={409: {}, 429: {}, 502: {}, 503: {}},
)
async def mowl_playlists(
    request: Request, ctx: Annotated[UserContext, Depends(auth.require_user)]
) -> PlaylistsOut | JSONResponse:
    failure: ConnectionFailure | None = None
    answer: object = None
    try:
        answer = await use(
            request.app.state.session_factory, request.app.state.keyring, ctx, "mowl", _read
        )
    except ConnectionFailure as error:
        failure = error
    if failure is not None:
        headers: dict[str, str] = {}
        if failure.category == "rate_limited":
            headers["Retry-After"] = str(failure.retry_after or DEFAULT_RETRY_AFTER)
        return JSONResponse(
            {"detail": {"reason": failure.category}},
            status_code=_STATUS.get(failure.category, 502),
            headers=headers,
        )
    return PlaylistsOut(playlists=_listed(answer))
