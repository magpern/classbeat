"""``/api/classes/{id}/preview/spotify/alignment`` and ``.../corrections/{song}``: the Spotify
draft's alignment facts and the instructor's corrections (ADR 0042 sections 2 and 7.4; the rules
are ``classbeat.classes.spotify_corrections``'s). Web only. No Spotify call and no token: it reads
the class's own records.

| Route | Does |
|---|---|
| ``GET .../alignment`` | each song's length, spotdown's alignment and the class's correction |
| ``PUT .../corrections/{song}`` ``{revision_id, offset_ms}`` | sets or confirms one correction |
| ``DELETE .../corrections/{song}?revision_id=`` | clears it |

Signed in; with ``CLASSBEAT_SONG_PICKING`` off every route is 404 ``feature_off``.
The ``PUT`` and ``DELETE`` are CSRF-checked by the middleware. Another user's class, an unknown id
and a malformed one are the same 404 ``not_found``. Errors carry a reason code only. Every answer
is ``Cache-Control: no-store`` and holds no Spotify id, no URL and no token: a song is named by its
position, title and artist, which the class page shows already.
"""

from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Path, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from classbeat import auth
from classbeat.classes import spotify_corrections as corrections
from classbeat.classes.preview_spotify_routes import require_song_picking
from classbeat.classes.spotify_alignment_model import MAX_OFFSET_MS
from classbeat.classes.spotify_corrections import SongView, View
from classbeat.context import UserContext

NO_STORE = {"Cache-Control": "no-store"}
SongNumber = Annotated[int, Path(ge=1, le=corrections.MAX_ROWS)]

router = APIRouter(
    prefix="/api/classes",
    tags=["classes"],
    dependencies=[Depends(require_song_picking)],
)


class CorrectionIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    revision_id: UUID
    offset_ms: Annotated[int, Field(strict=True, ge=-MAX_OFFSET_MS, le=MAX_OFFSET_MS)]


def _uuid(raw: str) -> UUID | None:
    try:
        return UUID(raw)
    except ValueError:
        return None


def _refuse(reason: str, status: int = 409) -> JSONResponse:
    return JSONResponse({"detail": {"reason": reason}}, status_code=status, headers=NO_STORE)


def _song(s: SongView) -> dict[str, Any]:
    fact = s.fact
    correction = s.correction
    return {
        "piece": s.piece,
        "song": s.song,
        "title": s.title,
        "artist": s.artist,
        "length_ms": s.length_ms,
        # spotdown's facts, as recorded: never changed by a correction.
        "analysis": {
            "spotdown_job": None if fact is None else fact.analysis_id,
            "sha256": None if fact is None else fact.sha256,
            "timing_class": None if fact is None else fact.timing_class,
        },
        "alignment": {
            "offset_ms": None if fact is None else fact.offset_ms,
            "source": "spotdown",
        },
        # the instructor's, beside them: with what it was set against and whether it still holds.
        "correction": None
        if correction is None
        else {
            "offset_ms": correction.offset_ms,
            "set_against_analysis": None
            if correction.set_against is None
            else correction.set_against.stored(),
            "state": correction.state,
        },
    }


def _answer(found: View) -> JSONResponse:
    body = {
        "revision_id": str(found.revision_id),
        "map_version": found.map_version,
        "crossfade_ms": found.crossfade_ms,
        "songs": [_song(s) for s in found.songs],
    }
    return JSONResponse(body, headers=NO_STORE)


def _changed(done: corrections.Changed) -> JSONResponse:
    if done.reason == "not_found":
        return _refuse("not_found", 404)
    if done.reason == "invalid_input":
        return _refuse("invalid_input", 422)
    if done.reason is not None or done.view is None:
        return _refuse(done.reason or "internal")
    return _answer(done.view)


@router.get("/{class_id}/preview/spotify/alignment", responses={404: {}, 409: {}})
async def alignment(
    class_id: str, request: Request, ctx: Annotated[UserContext, Depends(auth.require_user)]
) -> JSONResponse:
    wanted = _uuid(class_id)
    if wanted is None:
        return _refuse("not_found", 404)
    found = await corrections.view(request.app.state.session_factory, ctx, wanted)
    if found == "not_found":
        return _refuse("not_found", 404)
    return _refuse(found) if isinstance(found, str) else _answer(found)


@router.put("/{class_id}/preview/spotify/corrections/{song}", responses={404: {}, 409: {}, 422: {}})
async def set_correction(
    class_id: str,
    song: SongNumber,
    body: CorrectionIn,
    request: Request,
    ctx: Annotated[UserContext, Depends(auth.require_user)],
) -> JSONResponse:
    """Set (or confirm again) one song's correction for the current draft's music."""
    wanted = _uuid(class_id)
    if wanted is None:
        return _refuse("not_found", 404)
    done = await corrections.set_offset(
        request.app.state.session_factory, ctx, wanted, body.revision_id, song, body.offset_ms
    )
    return _changed(done)


@router.delete(
    "/{class_id}/preview/spotify/corrections/{song}", responses={404: {}, 409: {}, 422: {}}
)
async def clear_correction(
    class_id: str,
    song: SongNumber,
    revision_id: UUID,
    request: Request,
    ctx: Annotated[UserContext, Depends(auth.require_user)],
) -> JSONResponse:
    """Clear one song's correction. Idempotent."""
    wanted = _uuid(class_id)
    if wanted is None:
        return _refuse("not_found", 404)
    done = await corrections.clear_offset(
        request.app.state.session_factory, ctx, wanted, revision_id, song
    )
    return _changed(done)
