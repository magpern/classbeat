"""The environment's bounds on every plan and the period they count in (ADR 0011 sections 3, 4).

**Bounds** (stricter wins): ``CLASSBEAT_CLASSES_PER_MONTH_MAX`` and
``CLASSBEAT_MONTHLY_COST_MICROS_MAX`` bound a plan's money limits, ``CLASSBEAT_JOBS_PER_USER``
its concurrent jobs and ``CLASSBEAT_REVISIONS_PER_CLASS`` its revisions per class. The
effective value of each limit is the smaller of the plan row and the bound; ``effective`` is the
one place that takes it, for the ledger, the policies, ``GET /api/account/plan`` and the admin
user page alike.

**The period** is the UTC calendar month (ADR 0011 section 3), read from the database's clock in
the caller's transaction: ``period`` its first day, ``period_end`` the first day of the next
month (exclusive), and the period's model cost (section 4) is the sum of the user's
``agent_runs.cost_micros`` whose ``started_at`` falls in it.

Web and worker are separate processes: the web app calls ``configure`` when it is created
(``classbeat.web``), so the revise kinds' ``admit`` reads the bounds; without it the settings'
defaults apply.
"""

from dataclasses import dataclass
from datetime import date
from typing import Final

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from classbeat.config import Settings
from classbeat.context import UserContext, require_context
from classbeat.plans.resolve import Plan

DEFAULT_CLASSES_MAX: Final = 200
DEFAULT_COST_MAX: Final = 300_000_000
DEFAULT_JOBS: Final = 2
DEFAULT_REVISIONS: Final = 25

_PERIOD = text("SELECT CAST(date_trunc('month', now() AT TIME ZONE 'UTC') AS date) AS period")
_COST = text(
    "SELECT coalesce(sum(cost_micros), 0) FROM agent_runs WHERE owner_id = :o "
    "AND started_at >= CAST(:p AS timestamp) AT TIME ZONE 'UTC' "
    "AND started_at < (CAST(:p AS timestamp) + interval '1 month') AT TIME ZONE 'UTC'"
)


@dataclass(frozen=True, slots=True)
class Bounds:
    classes_per_month_max: int = DEFAULT_CLASSES_MAX
    monthly_cost_micros_max: int = DEFAULT_COST_MAX
    jobs_per_user: int = DEFAULT_JOBS
    revisions_per_class: int = DEFAULT_REVISIONS

    @classmethod
    def from_settings(cls, settings: Settings) -> "Bounds":
        return cls(
            classes_per_month_max=settings.classes_per_month_max,
            monthly_cost_micros_max=settings.monthly_cost_micros_max,
            jobs_per_user=settings.jobs_per_user,
            revisions_per_class=settings.revisions_per_class,
        )


@dataclass(frozen=True, slots=True)
class Effective:
    """A plan's limits under the bounds."""

    classes_per_month: int
    monthly_cost_micros: int
    concurrent_jobs: int
    revisions_per_class: int


def effective_classes(stored: int, bounds: Bounds) -> int:
    """A plan's effective ``classes_per_month`` from its stored value (the admin user page)."""
    return min(stored, bounds.classes_per_month_max)


def effective(plan: Plan, bounds: Bounds) -> Effective:
    return Effective(
        classes_per_month=effective_classes(plan.classes_per_month, bounds),
        monthly_cost_micros=min(plan.monthly_cost_micros, bounds.monthly_cost_micros_max),
        concurrent_jobs=min(plan.concurrent_jobs, bounds.jobs_per_user),
        revisions_per_class=min(plan.revisions_per_class, bounds.revisions_per_class),
    )


def next_month(first: date) -> date:
    return date(first.year + first.month // 12, first.month % 12 + 1, 1)


async def period(db: AsyncSession) -> date:
    """The first day of the current UTC month, by the database's clock."""
    return (await db.execute(_PERIOD)).scalar_one()


async def period_cost(db: AsyncSession, ctx: UserContext, first: date) -> int:
    """The user's model cost in the period that starts on ``first``, in micro-dollars."""
    ctx = require_context(ctx)
    return int((await db.execute(_COST, {"o": ctx.user_id, "p": first})).scalar_one())


_bounds = Bounds()


def current() -> Bounds:
    return _bounds


def configure(settings: Settings | None = None) -> None:
    """Set the bounds from the settings (None: the defaults)."""
    global _bounds
    _bounds = Bounds() if settings is None else Bounds.from_settings(settings)
