"""``/api/classes/{id}``: the instructor's own edits of the current draft, and the pick of the
current revision (ADR 0007 section 2, ADR 0042 sections 3 to 6; issues C2 and #1391). Split out of
``classes.routes``.

Signed in; the ``POST``s are CSRF-checked by the middleware. Another user's id, an unknown id and a
malformed one are the same 404 ``not_found``. Errors carry a reason code only; ``stale_revision``
(409) also carries the class's ``current_revision_id``, so the page reloads it.

- ``POST /{id}/edits`` (``classes.user_edit``): one edit, ``{base_revision_id, edit}``, stored as a
  new ``user_edit`` revision that becomes current. 409 reasons: ``stale_revision``, ``class_busy``,
  ``revision_limit``, ``edit_unavailable`` (not a studio draft), ``no_proposal``,
  ``music_changed``, ``mix_not_applied``, and each refusal of the edit itself
  (``studio_edit_ops``, ``workout_edit_policy``: ``block_locked``, ``nothing_changed``,
  ``class_length_out_of_tolerance``, ``plan_class_too_long`` …).
- ``GET /{id}/overlay`` (``classes.user_edit_view``): the records, the unapplied and orphaned ones,
  and the undo and redo picks.
- ``POST /{id}/current``: the pick; ``expected_current_id`` (an undo or redo) answers
  ``stale_revision`` when the class moved. Other 409 reasons: ``class_busy``, ``hash_mismatch``,
  ``not_ready``, ``quota`` (#496, the jobs API's body).
"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from classbeat import auth
from classbeat.classes import service, user_edit, user_edit_view
from classbeat.classes.answers import HASH, internal, not_found, refuse, uuid_of
from classbeat.classes.schemas import ClassOut, PickIn
from classbeat.classes.user_edit_model import EditIn, EditOut, OverlayOut, StepOut
from classbeat.classes.user_edit_out import records_out
from classbeat.context import UserContext
from classbeat.jobs.credits import QuotaRefused

router = APIRouter(prefix="/api/classes", tags=["classes"])


def _stale(current: UUID | None) -> JSONResponse:
    detail = {"reason": "stale_revision", "current_revision_id": current and str(current)}
    return JSONResponse({"detail": detail}, status_code=409)


@router.post("/{class_id}/edits", response_model=EditOut, responses={404: {}, 409: {}, 422: {}})
async def post_edit(
    class_id: str,
    body: EditIn,
    request: Request,
    ctx: Annotated[UserContext, Depends(auth.require_user)],
) -> EditOut | JSONResponse:
    wanted = uuid_of(class_id)
    if wanted is None:
        return not_found()
    done = await user_edit.edit(request.app.state.session_factory, ctx, wanted, body)
    if done.reason == "not_found":
        return not_found()
    if done.reason == "stale_revision":
        return _stale(done.current_revision_id)
    if done.reason == "internal":
        return internal()
    if done.reason is not None or done.stored is None or done.made is None:
        return refuse(done.reason or "internal")
    made, spec = done.made, done.made.spec
    return EditOut(
        revision_id=done.stored.revision_id,
        number=done.stored.number,
        content_hash=done.stored.content_hash.hex(),
        records=records_out(made.overlay.records, spec),
        unapplied=records_out(made.unapplied, spec),
        orphaned=records_out(made.orphaned, spec),
        hints=list(made.hints),
    )


@router.get("/{class_id}/overlay", response_model=OverlayOut, responses={404: {}, 409: {}})
async def get_overlay(
    class_id: str,
    request: Request,
    ctx: Annotated[UserContext, Depends(auth.require_user)],
) -> OverlayOut | JSONResponse:
    wanted = uuid_of(class_id)
    if wanted is None:
        return not_found()
    found = await user_edit_view.view(request.app.state.session_factory, ctx, wanted)
    if found == "not_found":
        return not_found()
    if found == "internal":
        return internal()
    if isinstance(found, str):
        return refuse(found)

    def step(s: user_edit_view.Step | None) -> StepOut | None:
        return (
            None if s is None else StepOut(revision_id=s.revision_id, content_hash=s.content_hash)
        )

    return OverlayOut(
        revision_id=found.revision_id,
        records=found.records,
        unapplied=found.unapplied,
        orphaned=found.orphaned,
        undo=step(found.undo),
        redo=step(found.redo),
    )


@router.post("/{class_id}/current", response_model=ClassOut, responses={404: {}, 409: {}, 422: {}})
async def pick_current(
    class_id: str,
    body: PickIn,
    request: Request,
    ctx: Annotated[UserContext, Depends(auth.require_user)],
) -> ClassOut | JSONResponse:
    wanted = uuid_of(class_id)
    if wanted is None:
        return not_found()
    if not HASH.fullmatch(body.content_hash):
        return refuse("invalid_input", 422)
    factory = request.app.state.session_factory
    try:
        done = await service.pick(
            factory,
            ctx,
            wanted,
            body.revision_id,
            bytes.fromhex(body.content_hash),
            body.expected_current_id,
        )
    except QuotaRefused as refused:  # the current plan (#496); nothing changed
        return JSONResponse({"detail": {**refused.detail(), "reason": "quota"}}, status_code=409)
    if done.reason == "not_found":
        return not_found()
    if done.reason == "stale_revision":
        return _stale(done.current_revision_id)
    if done.reason is not None:
        return refuse(done.reason)
    row = await service.get_class(factory, ctx, wanted)
    return not_found() if row is None else ClassOut.of(row)
