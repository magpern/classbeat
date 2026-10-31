"""The user's plan and usage (ADR 0011 section 9): ``GET /api/account/plan``.

One read-only ``user_session`` (row security answers for the user; every statement also names
``owner_id``): the effective plan (``classbeat.plans.resolve``), the entitlement it comes from
(its source and end), the limits, the classes held in this UTC month and when it ends, this
month's model cost as a share of the plan's cap (never a dollar figure), the effective features
and whether a trial is available.

**Source and end.** Among the active entitlements naming the effective plan, the one that lasts
longest (no end first, then the latest end); ``None`` for ``free`` without an entitlement.
Overlapping entitlements don't add up, so that end is when the plan stops applying, unless a
lower-ranked one takes over.

**Limits** are the plan's under the environment's bounds (``classbeat.quota.effective``, the
same values the ledger and the policies use): ``CLASSBEAT_JOBS_PER_USER``,
``CLASSBEAT_REVISIONS_PER_CLASS``, ``CLASSBEAT_CLASSES_PER_MONTH_MAX`` and, for the cost share,
``CLASSBEAT_MONTHLY_COST_MICROS_MAX`` (ADR 0011 sections 3 and 4).

**Held** is ADR 0011 section 3's count: ledger rows of the period that are ``committed``, or
``reserved`` with a job.
"""

import math
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from classbeat.context import UserContext
from classbeat.db import user_session
from classbeat.plans.resolve import effective_features, effective_plan
from classbeat.plans.trial import TrialOffer, had_trial, trial_offer
from classbeat.quota.bounds import Bounds, effective

_SOURCE = text(
    "SELECT e.source, e.ends_at FROM entitlements e WHERE e.owner_id = :o "
    "AND e.plan_id = :p AND e.revoked_at IS NULL AND e.starts_at <= now() "
    "AND (e.ends_at IS NULL OR e.ends_at > now()) "
    "ORDER BY e.ends_at DESC NULLS FIRST LIMIT 1"
)
_PERIOD = (
    "date_trunc('month', now() AT TIME ZONE 'UTC') AT TIME ZONE 'UTC'",
    "(date_trunc('month', now() AT TIME ZONE 'UTC') + interval '1 month') AT TIME ZONE 'UTC'",
)
_BOUNDS = text(f"SELECT {_PERIOD[0]} AS starts, {_PERIOD[1]} AS ends")
_HELD = text(
    "SELECT count(*) FROM credit_ledger WHERE owner_id = :o "
    "AND period = CAST(date_trunc('month', now() AT TIME ZONE 'UTC') AS date) "
    "AND (state = 'committed' OR (state = 'reserved' AND job_id IS NOT NULL))"
)
_COST = text(
    "SELECT coalesce(sum(cost_micros), 0) FROM agent_runs "
    "WHERE owner_id = :o AND started_at >= :starts AND started_at < :ends"
)


@dataclass(frozen=True, slots=True)
class Limits:
    classes_per_month: int
    revisions_per_class: int
    concurrent_jobs: int


@dataclass(frozen=True, slots=True)
class AccountPlan:
    plan_id: str
    plan_name: str
    source: str | None
    ends_at: datetime | None
    limits: Limits
    classes_held: int
    period_end: datetime
    cost_share: float  # 0.0 to 1.0, rounded down to a hundredth
    features: tuple[str, ...]
    trial: TrialOffer | None  # the trial the user could start now, or None


def cost_share(cost_micros: int, cap_micros: int) -> float:
    """This period's cost as a share of the cap, rounded down to 0.01 and at most 1.0."""
    if cap_micros <= 0:
        return 1.0 if cost_micros > 0 else 0.0
    return min(1.0, math.floor(100 * cost_micros / cap_micros) / 100)


async def _account_plan(
    db: AsyncSession, ctx: UserContext, *, bounds: Bounds, trial_days_max: int
) -> AccountPlan:
    plan = await effective_plan(db, ctx)
    features = await effective_features(db, ctx)
    source = (await db.execute(_SOURCE, {"o": ctx.user_id, "p": plan.id})).one_or_none()
    span = (await db.execute(_BOUNDS)).one()
    held = (await db.execute(_HELD, {"o": ctx.user_id})).scalar_one()
    cost = (
        await db.execute(_COST, {"o": ctx.user_id, "starts": span.starts, "ends": span.ends})
    ).scalar_one()
    offer = None if await had_trial(db, ctx) else await trial_offer(db, trial_days_max)
    limits = effective(plan, bounds)
    return AccountPlan(
        plan_id=plan.id,
        plan_name=plan.name,
        source=None if source is None else source.source,
        ends_at=None if source is None else source.ends_at,
        limits=Limits(
            classes_per_month=limits.classes_per_month,
            revisions_per_class=limits.revisions_per_class,
            concurrent_jobs=limits.concurrent_jobs,
        ),
        classes_held=int(held),
        period_end=span.ends,
        cost_share=cost_share(int(cost), limits.monthly_cost_micros),
        features=tuple(sorted(features)),
        trial=offer,
    )


async def account_plan(
    factory: async_sessionmaker[AsyncSession],
    ctx: UserContext,
    *,
    bounds: Bounds,
    trial_days_max: int,
) -> AccountPlan:
    async with user_session(factory, ctx) as db:
        return await _account_plan(db, ctx, bounds=bounds, trial_days_max=trial_days_max)
