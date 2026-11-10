"""``/api/classes/{id}/interpretation`` and ``/api/classes/{id}/design-from-interpretation`` (ADR
0029 section 5; ``classbeat.classes.interpretation``).

- ``GET .../interpretation``: how the request was read (the items, the sport, the music's source),
  the stored hand-off refusal and the design job of the hand-off, if any. 404 ``not_found`` while
  for no such class of the user's; 409 ``not_ready`` while the interpret job has stored none.
- ``POST .../design-from-interpretation`` with an optional ``{"retry": true}``: 201 with the new
  design job ``{job_id, kind, state}``, 200 with the same job for every later call; 409
  ``{reason, ...counts}`` with the refusal, which is stored and answered again to every call until
  one with ``retry`` tries once more; 409 ``not_ready`` while there is no interpretation yet; 404
  ``not_found``. No ``Idempotency-Key`` header: the key is derived from the interpret job.

The body is a reason code and whole numbers only; another user's class is 404 like an unknown one.
"""

from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, ValidationError

from classbeat import auth
from classbeat.agent.brief import Brief
from classbeat.agent.interpretation_record import InterpretationItem
from classbeat.classes import interpretation
from classbeat.classes.handoff_routes import job_enqueue
from classbeat.context import UserContext
from classbeat.jobs.credits import CreditLedger
from classbeat.jobs.http import credit_ledger, job_limit
from classbeat.jobs.user import JobLimitPolicy

router = APIRouter(prefix="/api/classes", tags=["classes"])


class HandoffIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    retry: bool = False


class DesignJobOut(BaseModel):
    job_id: UUID
    kind: str
    state: str


class RefusalOut(BaseModel):
    reason: str
    counts: dict[str, int | str] = {}


class InterpretationOut(BaseModel):
    interpret_job_id: UUID
    sport: str
    music: str | None  # the music's source; None for a class with no music that could be chosen
    items: list[InterpretationItem]
    refusal: RefusalOut | None = None
    design_job: DesignJobOut | None = None


def _refuse(reason: str, counts: dict[str, Any] | None = None) -> JSONResponse:
    status = 404 if reason == "not_found" else 409
    return JSONResponse({"detail": {"reason": reason, **(counts or {})}}, status_code=status)


def _uuid(raw: str) -> UUID | None:
    try:
        return UUID(raw)
    except ValueError:
        return None


@router.get(
    "/{class_id}/interpretation", response_model=InterpretationOut, responses={404: {}, 409: {}}
)
async def get_interpretation(
    class_id: str, request: Request, ctx: Annotated[UserContext, Depends(auth.require_user)]
) -> InterpretationOut | JSONResponse:
    wanted = _uuid(class_id)
    if wanted is None:
        return _refuse("not_found")
    found = await interpretation.read_view(request.app.state.session_factory, ctx, wanted)
    if isinstance(found, str):
        return _refuse(found)
    row = found.interpretation
    try:
        sport = Brief.model_validate(row.brief).sport
    except ValidationError:
        return JSONResponse({"detail": {"reason": "internal"}}, status_code=500)
    design = found.design
    job = None
    if design is not None and design.job_id and design.kind and design.state:
        job = DesignJobOut(job_id=design.job_id, kind=design.kind, state=design.state)
    refusal = None
    if row.refusal is not None:
        refusal = RefusalOut(reason=row.refusal.reason, counts=dict(row.refusal.counts))
    return InterpretationOut(
        interpret_job_id=row.job_id,
        sport=sport,
        music=None if row.music is None else row.music.get("source"),
        items=list(row.items),
        refusal=refusal,
        design_job=job,
    )


@router.post(
    "/{class_id}/design-from-interpretation",
    status_code=201,
    response_model=DesignJobOut,
    responses={200: {"model": DesignJobOut}, 404: {}, 409: {}, 422: {}},
)
async def design_from_interpretation(
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
    done = await interpretation.design_from_interpretation(
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
