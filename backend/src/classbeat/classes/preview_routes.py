"""``/api/classes/{class_id}/preview``: the SwedeBeat audition's listening sessions and clips
(ADR 0042 sections 6.3 to 6.5; ``classes.preview_sessions``, ``classes.preview_clips``).

- ``POST .../preview/sessions`` with no body (the Play gesture): 201 ``{session_id, expires_at}``;
  409 ``no_studio_draft``; 429 ``rate_limited``; 404 ``not_found`` (another user's or an unknown
  class, and with the audition off).
- ``GET .../preview/clip?session=<id>&start_ms=<n>``: 200 the clip (``audio/mpeg``, ``Range``
  served, ``Content-Disposition: inline``, ``Cache-Control: private, no-store``, the window in
  ``X-Preview-Start-Ms`` and ``X-Preview-End-Ms``); 202 ``{state: "rendering", job_id, start_ms,
  end_ms}`` with ``Retry-After`` while it renders (ask again); **404** for another user's or
  another class's session, an unknown one or the audition off; **410**
  ``preview_session_ended`` past the session's 30 min or its 25 min of delivered audio; 422
  ``invalid_start``; 409 a draft reason or ``render_failed`` with the job's ``category``; 429
  ``rate_limited``.

The clip request is a ``GET`` (an ``audio`` element cannot send the CSRF header): it is authorized
by the signed-in user and an unguessable session id of their own, and what it changes is only that
session's count and the user's own render queue. **Not DRM**: an authorized user can save what the
browser plays (ADR 0042 section 6.3).
"""

from typing import Annotated, Final

from fastapi import APIRouter, Depends, FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, Response

from classbeat import auth
from classbeat.classes import preview_clips, preview_sessions
from classbeat.classes.studio_songs_routes import _uuid  # pyright: ignore[reportPrivateUsage]
from classbeat.config import Settings
from classbeat.context import UserContext
from classbeat.preview import storage

router = APIRouter(prefix="/api/classes", tags=["classes"])

CLIP_CACHE: Final = "private, no-store"
RETRY_SECONDS: Final = "2"
MAX_START_MS: Final = 24 * 3600 * 1000
_STATUS: Final = {
    "not_found": 404,
    "feature_off": 404,
    "ended": 410,
    "invalid_start": 422,
    "rate_limited": 429,
}


def _refuse(reason: str, category: str | None = None) -> JSONResponse:
    if reason == "feature_off":
        reason = "not_found"  # the audition off answers like an unknown path
    status = _STATUS.get(reason, 409)
    if reason == "ended":
        reason = "preview_session_ended"
    detail: dict[str, str] = {"reason": reason}
    if category is not None:
        detail["category"] = category
    return JSONResponse({"detail": detail}, status_code=status)


@router.post("/{class_id}/preview/sessions", status_code=201, responses={404: {}, 409: {}})
async def create_session(
    class_id: str,
    request: Request,
    ctx: Annotated[UserContext, Depends(auth.require_user)],
) -> JSONResponse:
    wanted = _uuid(class_id)
    if wanted is None:
        return _refuse("not_found")
    made = await preview_sessions.create(request.app.state.session_factory, ctx, wanted)
    if isinstance(made, str):
        return _refuse(made)
    body = {"session_id": str(made.id), "expires_at": made.expires_at.isoformat()}
    return JSONResponse(body, status_code=201)


@router.get("/{class_id}/preview/clip", responses={202: {}, 404: {}, 409: {}, 410: {}})
async def get_clip(
    class_id: str,
    request: Request,
    ctx: Annotated[UserContext, Depends(auth.require_user)],
    session: str = "",
    start_ms: str = "",
) -> Response:
    wanted, session_id = _uuid(class_id), _uuid(session)
    if wanted is None or session_id is None:
        return _refuse("not_found")
    if not start_ms.isascii() or not start_ms.isdigit() or int(start_ms) > MAX_START_MS:
        return _refuse("invalid_start")
    found = await preview_clips.clip(
        request.app.state.session_factory, ctx, wanted, session_id, int(start_ms)
    )
    if isinstance(found, preview_clips.Refused):
        return _refuse(found.reason, found.category)
    window = {"start_ms": found.window.start_ms, "end_ms": found.window.end_ms}
    if isinstance(found, preview_clips.Rendering):
        body = {"state": "rendering", "job_id": str(found.job_id), **window}
        return JSONResponse(body, status_code=202, headers={"Retry-After": RETRY_SECONDS})
    return FileResponse(
        found.path,
        media_type="audio/mpeg",
        headers={
            "Content-Disposition": "inline",
            "Cache-Control": CLIP_CACHE,
            "X-Preview-Start-Ms": str(found.window.start_ms),
            "X-Preview-End-Ms": str(found.window.end_ms),
        },
    )


def install(app: FastAPI, settings: Settings) -> None:
    """The preview volume from the settings, and these routes."""
    storage.configure_from(settings.preview_dir, settings.preview_owner_max_mb)
    app.include_router(router)
