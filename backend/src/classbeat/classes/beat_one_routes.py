"""``/api/classes/{id}/beat-one``: the beat-one corrections of a class (ADR 0007 amendment
"beat-one corrections"), split out of ``classbeat.classes.routes`` (CLAUDE.md, split on touch).

Signed in; the ``PUT`` and ``DELETE`` are CSRF-checked by the middleware. Another user's id, an
unknown id and a malformed one are the same 404 ``not_found``. Errors carry a reason code only.
"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Path, Request
from fastapi.responses import JSONResponse

from classbeat import auth
from classbeat.classes import beat_one
from classbeat.classes.schemas import BeatOneIn, BeatOneOut
from classbeat.context import UserContext

router = APIRouter(prefix="/api/classes", tags=["classes"])


def _refuse(reason: str, status: int = 409) -> JSONResponse:
    return JSONResponse({"detail": {"reason": reason}}, status_code=status)


def _not_found() -> JSONResponse:
    return _refuse("not_found", 404)


def _uuid(raw: str) -> UUID | None:
    try:
        return UUID(raw)
    except ValueError:
        return None


def _beat_one_answer(done: beat_one.Changed) -> BeatOneOut | JSONResponse:
    if done.reason == "not_found":
        return _not_found()
    if done.reason == "invalid_input":
        return _refuse("invalid_input", 422)
    if done.reason is not None:
        return _refuse(done.reason)
    return BeatOneOut.of(done.view)


async def _beat_one(
    class_id: str, song: int, revision_id: UUID, beats: int | None, request: Request,
    ctx: UserContext,
) -> BeatOneOut | JSONResponse:  # fmt: skip
    wanted = _uuid(class_id)
    if wanted is None:
        return _not_found()
    done = await beat_one.change(
        request.app.state.session_factory, ctx, wanted, revision_id, song, beats
    )
    return _beat_one_answer(done)


@router.put(
    "/{class_id}/beat-one/{song}",
    response_model=BeatOneOut,
    responses={404: {}, 409: {}, 422: {}},
)
async def set_beat_one(
    class_id: str,
    song: Annotated[int, Path(ge=1, le=500)],
    body: BeatOneIn,
    request: Request,
    ctx: Annotated[UserContext, Depends(auth.require_user)],
) -> BeatOneOut | JSONResponse:
    """Set one song's beat-one correction for the class's current revision's music (ADR 0007
    amendment "beat-one corrections"). It reaches MOWL only through a new revision."""
    return await _beat_one(class_id, song, body.revision_id, body.beats, request, ctx)


@router.delete(
    "/{class_id}/beat-one/rows/{row_id}",
    response_model=BeatOneOut,
    responses={404: {}},
)
async def clear_beat_one_row(
    class_id: str,
    row_id: str,
    request: Request,
    ctx: Annotated[UserContext, Depends(auth.require_user)],
) -> BeatOneOut | JSONResponse:
    """Clear any row of the class by its id: one for earlier music or for a song the current
    revision does not play. Another class's row is 404."""
    wanted, row = _uuid(class_id), _uuid(row_id)
    if wanted is None or row is None:
        return _not_found()
    done = await beat_one.clear_row(request.app.state.session_factory, ctx, wanted, row)
    return _beat_one_answer(done)


@router.delete(
    "/{class_id}/beat-one/{song}",
    response_model=BeatOneOut,
    responses={404: {}, 409: {}, 422: {}},
)
async def clear_beat_one(
    class_id: str,
    song: Annotated[int, Path(ge=1, le=500)],
    revision_id: UUID,
    request: Request,
    ctx: Annotated[UserContext, Depends(auth.require_user)],
) -> BeatOneOut | JSONResponse:
    """Clear one song's correction for the current revision's music. Idempotent; a revision that
    is not current is 409 ``stale_base`` and nothing is deleted."""
    return await _beat_one(class_id, song, revision_id, None, request, ctx)


@router.delete("/{class_id}/beat-one", response_model=BeatOneOut, responses={404: {}})
async def clear_all_beat_one(
    class_id: str,
    request: Request,
    ctx: Annotated[UserContext, Depends(auth.require_user)],
) -> BeatOneOut | JSONResponse:
    """Clear every beat-one correction of the class. Idempotent."""
    wanted = _uuid(class_id)
    if wanted is None:
        return _not_found()
    done = await beat_one.clear_all(request.app.state.session_factory, ctx, wanted)
    return _beat_one_answer(done)
