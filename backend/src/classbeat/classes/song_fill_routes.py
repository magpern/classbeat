"""``/api/classes/{id}/fill-songs`` (ADR 0038 section 5, #1188; ``classbeat.classes.song_fill``).

- ``GET .../fill-songs``: the fill of the class's current sketch: ``{sketch_revision_id, attempt,
  refusal, job}`` (``job`` is ``{job_id, kind, state}`` of the attempt's key, null before the first
  call). 404 ``not_found``; 409 ``not_ready`` while the current revision is no sketch.
- ``POST .../fill-songs`` with an optional ``{"retry": true}`` (**Find songs again**): 201 with the
  new fill job ``{job_id, kind, state}``, 200 with the same job for every later call of that
  attempt; 409 ``{reason, ...counts}`` with the refusal, stored and answered again until a call
  with ``retry``; 409 ``not_ready``, ``feature_off``, ``not_connected``, ``class_busy`` (never
  stored); 404 ``not_found``. No ``Idempotency-Key`` header: the key is derived from the class,
  the sketch and the attempt. No credit is used.

- ``POST .../swap-songs`` ``{song_list_revision_id, replace: [position]}`` with an
  ``Idempotency-Key`` header (``classbeat.classes.song_swap``): the swap that replaces the waiting
  list's job (ADR 0038 section 6): 201 ``{job_id, kind, state}``, 200 with the same job for the
  same key; 409 ``not_waiting``, ``expired``, ``revision_limit``, ``quota``, ``pool_busy`` and
  the rest of the create's refusals, each leaving the waiting list approvable; 422 for a body or
  key that does not parse.

Only while ``composer_flow`` is on. The body is a reason code and whole numbers only; another
user's class is 404 like an unknown one.
"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from classbeat import auth
from classbeat.classes import song_fill, song_swap
from classbeat.classes.handoff_routes import job_enqueue
from classbeat.classes.interpretation_routes import DesignJobOut, HandoffIn, RefusalOut
from classbeat.context import UserContext
from classbeat.jobs.credits import CreditLedger
from classbeat.jobs.http import IDEMPOTENCY_KEY, credit_ledger, job_limit
from classbeat.jobs.user import JobLimitPolicy

router = APIRouter(prefix="/api/classes", tags=["classes"])


def _refuse(reason: str, counts: dict[str, int | str] | None = None) -> JSONResponse:
    status = 404 if reason == "not_found" else 409
    return JSONResponse({"detail": {"reason": reason, **(counts or {})}}, status_code=status)


def _uuid(raw: str) -> UUID | None:
    try:
        return UUID(raw)
    except ValueError:
        return None


class FillOut(BaseModel):
    sketch_revision_id: UUID
    attempt: int
    refusal: RefusalOut | None = None
    job: DesignJobOut | None = None


@router.get("/{class_id}/fill-songs", response_model=FillOut, responses={404: {}, 409: {}})
async def get_fill(
    class_id: str, request: Request, ctx: Annotated[UserContext, Depends(auth.require_user)]
) -> FillOut | JSONResponse:
    wanted = _uuid(class_id)
    if wanted is None:
        return _refuse("not_found")
    found = await song_fill.read(request.app.state.session_factory, ctx, wanted)
    if isinstance(found, str):
        return _refuse(found)
    job = found.job
    return FillOut(
        sketch_revision_id=found.sketch_revision_id,
        attempt=found.attempt,
        refusal=None
        if found.refusal is None
        else RefusalOut(reason=found.refusal.reason, counts=dict(found.refusal.counts)),
        job=None
        if job is None or job.job_id is None or job.kind is None or job.state is None
        else DesignJobOut(job_id=job.job_id, kind=job.kind, state=job.state),
    )


@router.post(
    "/{class_id}/fill-songs",
    status_code=201,
    response_model=DesignJobOut,
    responses={200: {"model": DesignJobOut}, 404: {}, 409: {}, 422: {}},
)
async def fill_songs(
    class_id: str,
    request: Request,
    ctx: Annotated[UserContext, Depends(auth.require_user)],
    ledger: Annotated[CreditLedger, Depends(credit_ledger)],
    limit: Annotated[JobLimitPolicy, Depends(job_limit)],
) -> DesignJobOut | JSONResponse:
    wanted = _uuid(class_id)
    if wanted is None:
        return _refuse("not_found")
    try:
        raw = await request.body()
        body = HandoffIn.model_validate_json(raw or b"{}")
    except ValidationError:
        return JSONResponse({"detail": {"reason": "invalid_input"}}, status_code=422)
    done = await song_fill.start(
        request.app.state.session_factory,
        ctx,
        wanted,
        retry=body.retry,
        ledger=ledger,
        limit=limit,
        enqueue=job_enqueue(),
    )
    if done.job_id is None or done.kind is None or done.state is None:
        return _refuse(done.reason or "invalid_input", dict(done.counts))
    out = DesignJobOut(job_id=done.job_id, kind=done.kind, state=done.state)
    if not done.created:
        return JSONResponse(out.model_dump(mode="json"), status_code=200)
    return out


class SwapIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    song_list_revision_id: UUID
    replace: list[int] = Field(min_length=1, max_length=40)


@router.post(
    "/{class_id}/swap-songs",
    status_code=201,
    response_model=DesignJobOut,
    responses={200: {"model": DesignJobOut}, 404: {}, 409: {}, 422: {}},
)
async def swap_songs(
    class_id: str,
    request: Request,
    ctx: Annotated[UserContext, Depends(auth.require_user)],
    ledger: Annotated[CreditLedger, Depends(credit_ledger)],
    limit: Annotated[JobLimitPolicy, Depends(job_limit)],
    idempotency_key: Annotated[str | None, Header()] = None,
) -> DesignJobOut | JSONResponse:
    wanted = _uuid(class_id)
    if wanted is None:
        return _refuse("not_found")
    if idempotency_key is None or not IDEMPOTENCY_KEY.fullmatch(idempotency_key):
        return JSONResponse({"detail": {"reason": "invalid_input"}}, status_code=422)
    try:
        body = SwapIn.model_validate_json(await request.body())
    except ValidationError:
        return JSONResponse({"detail": {"reason": "invalid_input"}}, status_code=422)
    done = await song_swap.swap(
        request.app.state.session_factory,
        ctx,
        wanted,
        body.song_list_revision_id,
        body.replace,
        idempotency_key=idempotency_key,
        ledger=ledger,
        limit=limit,
        enqueue=job_enqueue(),
    )
    if done.job_id is None or done.kind is None or done.state is None:
        return _refuse(done.reason or "invalid_input", dict(done.counts))
    out = DesignJobOut(job_id=done.job_id, kind=done.kind, state=done.state)
    if not done.created:
        return JSONResponse(out.model_dump(mode="json"), status_code=200)
    return out
