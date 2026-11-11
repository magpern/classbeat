"""The plan catalogue the public pricing view shows (ADR 0031, M7a-2): ``GET /api/plans``.

Every plan in rank order with its id, name, rank, quality word, limits under the environment's
bounds (``classbeat.quota.effective``, the values a job is admitted under) and feature gates.
**No price is read or named**: the select list is ``classbeat.plans.resolve.PLAN_SELECT``, which
has no price column (the app role could not read one anyway, migration 0030). Prices stay with
``classbeat.admin``; M7a-3 reads them on the billing path only.
"""

from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from classbeat.plans.quality_model import Quality, quality_for
from classbeat.plans.resolve import PLAN_SELECT, plan_from_row
from classbeat.quota.bounds import Bounds, effective

_ALL = text(f"{PLAN_SELECT} ORDER BY rank")


@dataclass(frozen=True, slots=True)
class CataloguePlan:
    id: str
    name: str
    rank: int
    quality: Quality
    classes_per_month: int
    revisions_per_class: int
    concurrent_jobs: int
    max_class_minutes: int
    games: bool
    races: bool
    ftp_tests: bool


async def plan_catalogue(db: AsyncSession, bounds: Bounds) -> list[CataloguePlan]:
    """All plans, lowest rank first. ``db`` is a ``system_session`` (``plans`` has no owner)."""
    found: list[CataloguePlan] = []
    for row in (await db.execute(_ALL)).all():
        plan = plan_from_row(row)
        limits = effective(plan, bounds)
        found.append(
            CataloguePlan(
                id=plan.id,
                name=plan.name,
                rank=plan.rank,
                quality=quality_for(plan.id),
                classes_per_month=limits.classes_per_month,
                revisions_per_class=limits.revisions_per_class,
                concurrent_jobs=limits.concurrent_jobs,
                max_class_minutes=limits.max_class_minutes,
                games=plan.games,
                races=plan.races,
                ftp_tests=plan.ftp_tests,
            )
        )
    return found
