"""The effective plan and features of a user (ADR 0011 section 2).

Both functions read inside the caller's transaction (a ``user_session``, so row security answers
for the context's user), so a grant, revoke or expiry takes effect on the next request and a
reservation reads the plan under the same lock it counts under. Nothing is cached and
``UserContext.entitlements`` stays empty.

An entitlement is **active** when ``revoked_at IS NULL``, ``starts_at <= now()`` and ``ends_at``
is NULL or later than ``now()`` (the transaction's time). The effective plan is the
highest-ranked plan named by an active entitlement, ``free`` when none; the effective features are
that plan's features plus every active entitlement's. Overlapping entitlements don't add up.
"""

from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from classbeat.context import UserContext, require_context
from classbeat.plans.tables import FREE

_ACTIVE = (
    "e.owner_id = :owner_id AND e.revoked_at IS NULL AND e.starts_at <= now() "
    "AND (e.ends_at IS NULL OR e.ends_at > now())"
)
_PLAN = text(
    "SELECT id, name, rank, classes_per_month, revisions_per_class, concurrent_jobs, "  # noqa: S608
    "monthly_cost_micros, route_key, features FROM plans WHERE id = coalesce(("
    "SELECT p.id FROM entitlements e JOIN plans p ON p.id = e.plan_id "
    f"WHERE {_ACTIVE} ORDER BY p.rank DESC LIMIT 1), :free)"
)
_FEATURES = text(
    f"SELECT DISTINCT f FROM entitlements e, unnest(e.features) AS f WHERE {_ACTIVE}"  # noqa: S608
)


@dataclass(frozen=True, slots=True)
class Plan:
    """A row of ``plans``: the limits of a tier (before ADR 0011 section 4's env bounds)."""

    id: str
    name: str
    rank: int
    classes_per_month: int
    revisions_per_class: int
    concurrent_jobs: int
    monthly_cost_micros: int
    route_key: str
    features: frozenset[str]


async def effective_plan(db: AsyncSession, ctx: UserContext) -> Plan:
    """The user's effective plan; ``free`` when no active entitlement names a plan."""
    ctx = require_context(ctx)
    row = (
        await db.execute(_PLAN, {"owner_id": ctx.user_id, "free": FREE})
    ).one()  # no row means the ``free`` seed is missing: fail loudly, never a default
    return Plan(
        id=row.id,
        name=row.name,
        rank=row.rank,
        classes_per_month=row.classes_per_month,
        revisions_per_class=row.revisions_per_class,
        concurrent_jobs=row.concurrent_jobs,
        monthly_cost_micros=row.monthly_cost_micros,
        route_key=row.route_key,
        features=frozenset(row.features),
    )


async def effective_features(db: AsyncSession, ctx: UserContext) -> frozenset[str]:
    """The effective plan's features plus every active entitlement's."""
    ctx = require_context(ctx)
    plan = await effective_plan(db, ctx)
    granted = (await db.execute(_FEATURES, {"owner_id": ctx.user_id})).scalars().all()
    return plan.features | frozenset(granted)
