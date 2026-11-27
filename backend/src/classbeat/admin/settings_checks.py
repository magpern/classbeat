"""The checks a flag write makes before it is stored (ADR 0011 section 4): the trial's plan. Moved
out of ``classbeat.admin.settings`` (split on touch, #989); the route there calls them, in or
before its audited session. The running classes' coverage check went with the ``running_enabled``
key (ADR 0018 Amendment 6, #1544).
"""

from fastapi import Request
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from classbeat.admin.db import AdminRefused
from classbeat.db import system_session
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
