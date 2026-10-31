"""The user's trial (ADR 0011 sections 2, 4 and 7): ``POST /api/account/trial``.

The ``trial`` setting (``{plan, days}`` in ``app_settings``, read directly per request; FL1's
``classbeat.flags`` will own the setting later) says what a trial is. It is clamped **when used**,
as the plan limits are, so a stored value above a later-lowered bound never applies:

- ``days`` to ``CLASSBEAT_TRIAL_DAYS_MAX``; 0 (the default, or no row) is off;
- the plan to at most ``instructor``: a stored plan that ranks higher gives an Instructor trial.

A missing, malformed or unknown-plan setting is off (fail closed). One trial per account, ever:
the partial unique index decides, whatever the timing, and a second one is ``trial_used``.
"""

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from classbeat.context import UserContext
from classbeat.db import user_session
from classbeat.plans.entitle import TRIAL_MAX_PLAN, add_entitlement, plan_ranks

logger = logging.getLogger(__name__)

_SETTING = text("SELECT value FROM app_settings WHERE key = 'trial'")
_HAD_TRIAL = text(
    "SELECT EXISTS (SELECT 1 FROM entitlements WHERE owner_id = :o AND source = 'trial')"
)


@dataclass(frozen=True, slots=True)
class TrialOffer:
    """What a trial started now would be: the plan and length after clamping."""

    plan_id: str
    days: int


@dataclass(frozen=True, slots=True)
class TrialStarted:
    plan_id: str
    ends_at: datetime


TrialRefusal = Literal["trial_off", "trial_used"]


def _stored(value: Any) -> tuple[str, int] | None:
    if not isinstance(value, dict):
        return None
    plan, days = value.get("plan"), value.get("days")  # pyright: ignore[reportUnknownMemberType]
    if not isinstance(plan, str) or type(days) is not int or days < 0:
        return None
    return plan, days


async def trial_offer(db: AsyncSession, days_max: int) -> TrialOffer | None:
    """The clamped trial setting, or ``None`` when trials are off."""
    value = (await db.execute(_SETTING)).scalar_one_or_none()
    if value is None:
        return None
    stored = _stored(value)
    if stored is None:
        logger.warning("trial setting is malformed; trials are off")
        return None
    plan_id, days = stored
    days = min(days, days_max)
    if days <= 0:
        return None
    ranks = await plan_ranks(db, plan_id, TRIAL_MAX_PLAN)
    if plan_id not in ranks or TRIAL_MAX_PLAN not in ranks:
        logger.warning("trial setting names an unknown plan; trials are off")
        return None
    if ranks[plan_id] > ranks[TRIAL_MAX_PLAN]:
        plan_id = TRIAL_MAX_PLAN
    return TrialOffer(plan_id, days)


async def had_trial(db: AsyncSession, ctx: UserContext) -> bool:
    """Whether the account ever had a trial (revoked and ended ones too)."""
    return bool((await db.execute(_HAD_TRIAL, {"o": ctx.user_id})).scalar_one())


async def start_trial(
    factory: async_sessionmaker[AsyncSession], ctx: UserContext, days_max: int
) -> TrialStarted | TrialRefusal:
    """Start the user's trial, or say why not."""
    async with user_session(factory, ctx) as db:
        offer = await trial_offer(db, days_max)
        if offer is None:
            return "trial_off"
        added = await add_entitlement(
            db, ctx, source="trial", plan_id=offer.plan_id, days=offer.days
        )
        if added is None:
            return "trial_used"
        return TrialStarted(offer.plan_id, added.ends_at)
