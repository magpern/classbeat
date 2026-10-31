"""The plan's limits in the existing seams (ADR 0011 sections 3 and 4), read in the caller's
transaction so a grant, revoke or expiry applies to the next request.

- ``PlanRevisionPolicy`` (``classbeat.design.limits.RevisionPolicy``): the smaller of the plan's
  ``revisions_per_class`` and ``CLASSBEAT_REVISIONS_PER_CLASS``.
- ``PlanJobLimitPolicy`` (``classbeat.jobs.user.JobLimitPolicy``): the smaller of the plan's
  ``concurrent_jobs`` and ``CLASSBEAT_JOBS_PER_USER``, counted by the jobs API at create and
  approve under the owner lock.
"""

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from classbeat.context import UserContext
from classbeat.plans.resolve import effective_plan
from classbeat.quota.bounds import DEFAULT_JOBS, DEFAULT_REVISIONS


@dataclass(frozen=True, slots=True)
class PlanRevisionPolicy:
    revisions_per_class: int = DEFAULT_REVISIONS  # the environment's bound

    async def limit_for(self, db: AsyncSession, ctx: UserContext, class_id: UUID) -> int:
        plan = await effective_plan(db, ctx)
        return min(plan.revisions_per_class, self.revisions_per_class)


@dataclass(frozen=True, slots=True)
class PlanJobLimitPolicy:
    jobs_per_user: int = DEFAULT_JOBS  # the environment's bound

    async def limit_for(self, db: AsyncSession, ctx: UserContext) -> int:
        plan = await effective_plan(db, ctx)
        return min(plan.concurrent_jobs, self.jobs_per_user)
