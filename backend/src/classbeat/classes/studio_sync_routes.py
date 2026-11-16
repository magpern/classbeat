"""``/api/classes/{class_id}/spotify-sync/{revision_id}``: a studio revision's Spotify sync state
and "Sync again" (ADR 0036 section 9, SR C8; ``classbeat.classes.studio_sync``).

- ``GET``: ``{working_copy, final}``, each ``{state, detail}`` (``pending``, ``synced`` or
  ``failed`` with the failure's category) or null.
- ``POST .../working_copy`` and ``POST .../final`` with no body: 201 with the ``spotify_sync`` job
  ``{job_id, kind, state}``; 409 ``{reason}`` for ``not_failed`` (nothing to do: a repeated press)
  and ``not_connected``; 404 ``not_found`` (another user's or an unknown class or revision, and
  with the studio off).

Signed in (``auth.require_user``) and, like every unsafe request, with the CSRF token the auth
middleware checks.
"""

from typing import Annotated, get_args

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from classbeat import auth
from classbeat.classes import studio_sync
from classbeat.classes.studio_songs_routes import StudioJobOut, _uuid  # pyright: ignore[reportPrivateUsage]
from classbeat.context import UserContext
from classbeat.playlists.sync import Target
from classbeat.swedebeat_studio.sync_kind import KIND

router = APIRouter(prefix="/api/classes", tags=["classes"])

_STATUS = {"not_found": 404, "feature_off": 404}
_TARGETS: tuple[str, ...] = get_args(Target)


def _refuse(reason: str) -> JSONResponse:
    if reason == "feature_off":
        reason = "not_found"  # the studio off answers like an unknown path
    return JSONResponse({"detail": {"reason": reason}}, status_code=_STATUS.get(reason, 409))


@router.get("/{class_id}/spotify-sync/{revision_id}", responses={404: {}})
async def get_spotify_sync(
    class_id: str,
    revision_id: str,
    request: Request,
    ctx: Annotated[UserContext, Depends(auth.require_user)],
) -> JSONResponse:
    wanted, revision = _uuid(class_id), _uuid(revision_id)
    if wanted is None or revision is None:
        return _refuse("not_found")
    found = await studio_sync.view(request.app.state.session_factory, ctx, wanted, revision)
    if isinstance(found, str):
        return _refuse(found)
    return JSONResponse(found)


@router.post(
    "/{class_id}/spotify-sync/{revision_id}/{target}",
    status_code=201,
    response_model=StudioJobOut,
    responses={404: {}, 409: {}},
)
async def sync_again(
    class_id: str,
    revision_id: str,
    target: str,
    request: Request,
    ctx: Annotated[UserContext, Depends(auth.require_user)],
) -> StudioJobOut | JSONResponse:
    wanted, revision = _uuid(class_id), _uuid(revision_id)
    if wanted is None or revision is None or target not in _TARGETS:
        return _refuse("not_found")
    chosen: Target = "final" if target == "final" else "working_copy"
    done = await studio_sync.sync_again(
        request.app.state.session_factory, ctx, wanted, revision, chosen
    )
    if done.job_id is None:
        return _refuse(done.reason or "not_found")
    return StudioJobOut(job_id=done.job_id, kind=KIND, state="queued")
