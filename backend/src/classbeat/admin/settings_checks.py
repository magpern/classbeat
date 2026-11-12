"""The checks a flag write makes before it is stored (ADR 0011 section 4; #465): the trial's plan
and the running classes' model coverage. Moved out of ``classbeat.admin.settings`` (split on
touch, #989); the route there calls them, in or before its audited session.
"""

from fastapi import Request
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from classbeat.admin.db import AdminRefused
from classbeat.config import Settings
from classbeat.db import system_session
from classbeat.design.music_kind import fake_covers
from classbeat.models.routes import Variant, every_plan_covers
from classbeat.plans.entitle import TRIAL_MAX_PLAN, plan_ranks

_RANKS = text("SELECT id, rank FROM plans WHERE id = ANY(:ids)")


async def trial_plan_refusal(request: Request, plan: str) -> str | None:
    async with system_session(request.app.state.session_factory) as db:
        ranks = await plan_ranks(db, plan, TRIAL_MAX_PLAN)
    if plan not in ranks:
        return "unknown_plan"
    return "plan_above_instructor" if ranks[plan] > ranks[TRIAL_MAX_PLAN] else None


async def check_trial_plan(session: AsyncSession, plan: str) -> None:
    """The trial's plan exists and ranks at most ``instructor`` (ADR 0011 section 4)."""
    ranks = {
        r.id: r.rank for r in (await session.execute(_RANKS, {"ids": [plan, TRIAL_MAX_PLAN]})).all()
    }
    if plan not in ranks:
        raise AdminRefused("unknown_plan", 422)
    if ranks[plan] > ranks[TRIAL_MAX_PLAN]:
        raise AdminRefused("plan_above_instructor", 422)


async def covered(session: AsyncSession, settings: Settings, variant: Variant) -> bool:
    """Whether every plan's jobs would design ``variant`` classes on a model that covers them now:
    the web's own ``fake`` outside prod, or a covering ``design`` route for every ``route_key``."""
    return fake_covers(settings) or await every_plan_covers(session, variant)


async def check_running_evaluated(session: AsyncSession, settings: Settings) -> None:
    """``running_enabled`` is stored ``on`` only with coverage (#465). The effective flag checks
    it again on every read (#509), so a plan added or a route cleared later still turns it off;
    ``CLASSBEAT_RUNNING_ENABLED`` stays the kill switch, and the worker checks each job again."""
    if not await covered(session, settings, "running"):
        raise AdminRefused("running_not_evaluated", 422)
