"""``/api/classes``: my classes, their revisions and conversation, the pick of the current revision
and the class delete (ADR 0007 sections 2, 11 and 12; issue C2).

Signed in; the ``POST`` and ``DELETE`` are CSRF-checked by the middleware. Another user's id, an
unknown id and a malformed one are the same 404 ``not_found``. Errors carry a reason code only.
409 reasons: ``class_busy``, ``hash_mismatch`` (the pick) and ``call_may_be_running``,
``uncertain_write`` (the delete).
"""

import re
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from classbeat import auth
from classbeat.agent.schemas import WorkoutDraft
from classbeat.classes import service
from classbeat.classes.schemas import (
    ClassesOut,
    ClassOut,
    DeletedOut,
    MessageOut,
    MessagesOut,
    PickIn,
    ProvenanceOut,
    RevisionOut,
    RevisionsOut,
    RevisionSummaryOut,
    UnknownBpmOut,
)
from classbeat.context import UserContext

router = APIRouter(prefix="/api/classes", tags=["classes"])

HASH = re.compile(r"^[0-9a-f]{64}$")
KEPT_NOTE = "The workouts classbeat saved to your MOWL account stay there; delete them in MOWL."


def _refuse(reason: str, status: int = 409) -> JSONResponse:
    return JSONResponse({"detail": {"reason": reason}}, status_code=status)


def _not_found() -> JSONResponse:
    return _refuse("not_found", 404)


def _uuid(raw: str) -> UUID | None:
    try:
        return UUID(raw)
    except ValueError:
        return None


@router.get("", response_model=ClassesOut)
async def list_classes(
    request: Request, ctx: Annotated[UserContext, Depends(auth.require_user)]
) -> ClassesOut:
    rows = await service.list_classes(request.app.state.session_factory, ctx)
    return ClassesOut(classes=[ClassOut.of(r) for r in rows])


@router.get("/{class_id}", response_model=ClassOut, responses={404: {}})
async def get_class(
    class_id: str, request: Request, ctx: Annotated[UserContext, Depends(auth.require_user)]
) -> ClassOut | JSONResponse:
    wanted = _uuid(class_id)
    row = (
        None
        if wanted is None
        else await service.get_class(request.app.state.session_factory, ctx, wanted)
    )
    return _not_found() if row is None else ClassOut.of(row)


@router.get("/{class_id}/revisions", response_model=RevisionsOut, responses={404: {}})
async def list_revisions(
    class_id: str, request: Request, ctx: Annotated[UserContext, Depends(auth.require_user)]
) -> RevisionsOut | JSONResponse:
    wanted = _uuid(class_id)
    rows = (
        None
        if wanted is None
        else await service.list_revisions(request.app.state.session_factory, ctx, wanted)
    )
    if rows is None:
        return _not_found()
    return RevisionsOut(revisions=[RevisionSummaryOut.of(r) for r in rows])


@router.get("/{class_id}/revisions/{revision_id}", response_model=RevisionOut, responses={404: {}})
async def get_revision(
    class_id: str,
    revision_id: str,
    request: Request,
    ctx: Annotated[UserContext, Depends(auth.require_user)],
) -> RevisionOut | JSONResponse:
    wanted_class, wanted = _uuid(class_id), _uuid(revision_id)
    if wanted_class is None or wanted is None:
        return _not_found()
    row = await service.get_revision(request.app.state.session_factory, ctx, wanted_class, wanted)
    if row is None:
        return _not_found()
    try:
        draft = WorkoutDraft.model_validate(row.content)  # only keys the schema names are shown
        raw: Any = row.music.get("unknown_bpm", [])
        unknown = [UnknownBpmOut(index=int(u["index"]), title=str(u["title"])) for u in raw]
        provenance = ProvenanceOut.model_validate(row.provenance)
        summary = RevisionSummaryOut.of(row)
    except (ValidationError, KeyError, TypeError, ValueError):
        return JSONResponse({"detail": {"reason": "internal"}}, status_code=500)
    return RevisionOut(
        **summary.model_dump(),
        kind=row.kind,
        content=draft.model_dump(mode="json", exclude_none=True),
        unknown_bpm=unknown,
        provenance=provenance,
    )


@router.get("/{class_id}/messages", response_model=MessagesOut, responses={404: {}})
async def list_messages(
    class_id: str, request: Request, ctx: Annotated[UserContext, Depends(auth.require_user)]
) -> MessagesOut | JSONResponse:
    wanted = _uuid(class_id)
    rows = (
        None
        if wanted is None
        else await service.list_messages(request.app.state.session_factory, ctx, wanted)
    )
    if rows is None:
        return _not_found()
    return MessagesOut(
        messages=[
            MessageOut(
                seq=r.seq,
                role=r.role,
                body=r.body,
                job_id=r.job_id,
                base_revision_id=r.base_revision_id,
                result_revision_id=r.result_revision_id,
                change=r.change,
                created_at=r.created_at,
            )
            for r in rows
        ]
    )


@router.post("/{class_id}/current", response_model=ClassOut, responses={404: {}, 409: {}, 422: {}})
async def pick_current(
    class_id: str,
    body: PickIn,
    request: Request,
    ctx: Annotated[UserContext, Depends(auth.require_user)],
) -> ClassOut | JSONResponse:
    wanted = _uuid(class_id)
    if wanted is None:
        return _not_found()
    if not HASH.fullmatch(body.content_hash):
        return _refuse("invalid_input", 422)
    factory = request.app.state.session_factory
    done = await service.pick(
        factory, ctx, wanted, body.revision_id, bytes.fromhex(body.content_hash)
    )
    if done.reason == "not_found":
        return _not_found()
    if done.reason is not None:
        return _refuse(done.reason)
    row = await service.get_class(factory, ctx, wanted)
    return _not_found() if row is None else ClassOut.of(row)


@router.delete("/{class_id}", response_model=DeletedOut, responses={404: {}, 409: {}})
async def delete_class(
    class_id: str,
    request: Request,
    ctx: Annotated[UserContext, Depends(auth.require_user)],
    confirm_uncertain: bool = False,
) -> DeletedOut | JSONResponse:
    wanted = _uuid(class_id)
    if wanted is None:
        return _not_found()
    done = await service.delete(
        request.app.state.session_factory, ctx, wanted, confirm_uncertain=confirm_uncertain
    )
    if done.reason == "not_found":
        return _not_found()
    if done.reason is not None:
        return _refuse(done.reason)
    return DeletedOut(deleted=True, mowl_workouts_kept=done.kept, note=KEPT_NOTE)
