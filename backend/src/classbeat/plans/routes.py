"""The account plan API (ADR 0011 sections 2 and 9). Signed in; the POSTs pass the CSRF and
origin checks of ``classbeat.auth`` (a middleware, so no route can forget them).

- ``GET /api/account/plan``: the plan, its source and end, limits, classes held and the period's
  end, cost as a share of the cap, features, and the trial on offer (``null`` when none).
- ``POST /api/account/redeem {code}``: 10 attempts per user and 30 per IP and hour (ADR 0003
  section 9, reserved before anything else, every attempt counts); 400 ``code_invalid`` for
  every code that can't be redeemed, 409 ``already_redeemed`` for the user's own earlier one.
- ``POST /api/account/trial``: 409 ``trial_off`` or ``trial_used``.

No price or dollar figure anywhere: billing is off (ADR 0011 section 8).
"""

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from classbeat import auth
from classbeat.config import Settings
from classbeat.context import UserContext
from classbeat.plans.account import account_plan
from classbeat.plans.redeem import redeem_code
from classbeat.plans.trial import start_trial
from classbeat.quota.bounds import Bounds

router = APIRouter(prefix="/api/account", tags=["plan"])


class LimitsOut(BaseModel):
    classes_per_month: int
    revisions_per_class: int
    concurrent_jobs: int


class TrialOut(BaseModel):
    plan: str
    days: int


class PlanOut(BaseModel):
    plan: str
    name: str
    source: str | None
    ends_at: datetime | None
    limits: LimitsOut
    classes_held: int
    period_end: datetime
    cost_share: float
    features: list[str]
    trial: TrialOut | None


class RedeemIn(BaseModel):
    code: str = Field(max_length=64)


class EntitlementOut(BaseModel):
    plan: str | None
    features: list[str]
    ends_at: datetime


def _refusal(reason: str, status: int) -> JSONResponse:
    return JSONResponse({"detail": {"reason": reason}}, status_code=status)


@router.get("/plan", response_model=PlanOut)
async def get_plan(
    request: Request, ctx: Annotated[UserContext, Depends(auth.require_user)]
) -> PlanOut:
    settings: Settings = request.app.state.settings
    found = await account_plan(
        request.app.state.session_factory,
        ctx,
        bounds=Bounds.from_settings(settings),
        trial_days_max=settings.trial_days_max,
    )
    return PlanOut(
        plan=found.plan_id,
        name=found.plan_name,
        source=found.source,
        ends_at=found.ends_at,
        limits=LimitsOut(
            classes_per_month=found.limits.classes_per_month,
            revisions_per_class=found.limits.revisions_per_class,
            concurrent_jobs=found.limits.concurrent_jobs,
        ),
        classes_held=found.classes_held,
        period_end=found.period_end,
        cost_share=found.cost_share,
        features=list(found.features),
        trial=None
        if found.trial is None
        else TrialOut(plan=found.trial.plan_id, days=found.trial.days),
    )


@router.post(
    "/redeem", response_model=EntitlementOut, responses={400: {}, 409: {}, 422: {}, 429: {}}
)
async def post_redeem(
    request: Request,
    body: RedeemIn,
    ctx: Annotated[UserContext, Depends(auth.require_user)],
) -> EntitlementOut | JSONResponse:
    attempt = await request.app.state.limiter.begin(
        auth.redeem_buckets(str(ctx.user_id), auth.request_ip(request))
    )
    if not attempt.allowed:
        raise auth.RateLimited(attempt.retry_after)
    result = await redeem_code(request.app.state.session_factory, ctx, body.code)
    if isinstance(result, str):
        return _refusal(result, 400 if result == "code_invalid" else 409)
    return EntitlementOut(
        plan=result.plan_id, features=list(result.features), ends_at=result.ends_at
    )


@router.post("/trial", response_model=EntitlementOut, responses={409: {}})
async def post_trial(
    request: Request, ctx: Annotated[UserContext, Depends(auth.require_user)]
) -> EntitlementOut | JSONResponse:
    settings: Settings = request.app.state.settings
    result = await start_trial(request.app.state.session_factory, ctx, settings.trial_days_max)
    if isinstance(result, str):
        return _refusal(result, 409)
    return EntitlementOut(plan=result.plan_id, features=[], ends_at=result.ends_at)
