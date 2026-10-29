"""``GET /api/usage?month=YYYY-MM`` (signed in): the user's own usage in a UTC month (ADR 0007
section 10). Defaults to the current UTC month; 422 ``invalid_month`` for another shape. A month
without runs is all zeros (the 404 for a month before the account needs the account's creation
date, which only ``classbeat.auth`` may read). The ``notes`` codes are always present."""

from dataclasses import asdict
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from classbeat import auth
from classbeat.context import UserContext
from classbeat.usage import service

router = APIRouter(prefix="/api/usage", tags=["usage"])


class SumsOut(BaseModel):
    runs: int
    jobs: int
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    cache_write_tokens: int
    cost_micros: int


class KindModelOut(SumsOut):
    job_kind: str
    model: str


class UsageOut(BaseModel):
    month: str
    total: SumsOut
    by_kind_model: list[KindModelOut]
    classes_designed: int
    revisions: int
    notes: list[str]


@router.get("", response_model=UsageOut, responses={422: {}})
async def get_usage(
    request: Request,
    ctx: Annotated[UserContext, Depends(auth.require_user)],
    month: str | None = None,
) -> UsageOut | JSONResponse:
    wanted = month if month is not None else service.current_month()
    if service.month_bounds(wanted) is None:
        return JSONResponse({"detail": {"reason": "invalid_month"}}, status_code=422)
    found = await service.month_usage(request.app.state.session_factory, ctx, wanted)
    if found is None:  # unreachable after the shape check; kept so the type is narrowed
        return JSONResponse({"detail": {"reason": "invalid_month"}}, status_code=422)
    return UsageOut(
        month=found.month,
        total=SumsOut(**asdict(found.total)),
        by_kind_model=[
            KindModelOut(job_kind=r.job_kind, model=r.model, **asdict(r.sums))
            for r in found.by_kind_model
        ],
        classes_designed=found.classes_designed,
        revisions=found.revisions,
        notes=list(found.notes),
    )
