"""The effective plan and features of a user (ADR 0011 section 2).

Both functions read inside the caller's transaction (a ``user_session``, so row security answers
for the context's user), so a grant, revoke or expiry takes effect on the next request and a
reservation reads the plan under the same lock it counts under. Nothing is cached and
``UserContext.entitlements`` stays empty.

An entitlement is **active** when ``revoked_at IS NULL``, ``starts_at <= now()`` and ``ends_at``
is NULL or later than ``now()`` (the transaction's time). The effective plan is the
highest-ranked plan named by an active entitlement, ``free`` when none; the effective features are
that plan's features plus every active entitlement's. Overlapping entitlements don't add up.

**Admins (ADR 0031 section 2).** A user whose *stored* ``users.role`` is ``admin`` resolves to the
highest-ranked plan, entitlements or not. The role is read by ``classbeat.auth.stored_admin`` with
the caller's session in the caller's transaction (guard 8: this package never queries ``users``);
it is ``False`` for a disabled or missing user. The plan sets usage limits only: an admin's
features are still the plan's plus the entitlements', so the role grants no ``song_picking`` or
``billing_test``.
"""

from dataclasses import dataclass
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from classbeat import auth
from classbeat.context import UserContext, require_context
from classbeat.plans.tables import FREE

_ACTIVE = (
    "e.owner_id = :owner_id AND e.revoked_at IS NULL AND e.starts_at <= now() "
    "AND (e.ends_at IS NULL OR e.ends_at > now())"
)
PLAN_SELECT = (  # every column but the prices (migration 0030)
    "SELECT id, name, rank, classes_per_month, revisions_per_class, concurrent_jobs, "
    "monthly_cost_micros, route_key, features, max_class_minutes, games_allowed, races_allowed, "
    "ftp_tests_allowed, saves_per_hour, saves_per_day FROM plans"
)
_PLAN = text(
    f"{PLAN_SELECT} WHERE id = coalesce(("  # noqa: S608
    "SELECT p.id FROM entitlements e JOIN plans p ON p.id = e.plan_id "
    f"WHERE {_ACTIVE} ORDER BY p.rank DESC LIMIT 1), :free)"
)
_TOP_PLAN = text(f"{PLAN_SELECT} WHERE id = (SELECT id FROM plans ORDER BY rank DESC LIMIT 1)")  # noqa: S608
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
    # Migration 0026 (#496): the longest class and the feature gates. Prices are never read here:
    # only the admin area reads them (``classbeat.admin.settings``).
    max_class_minutes: int
    games: bool
    races: bool
    ftp_tests: bool
    # Migration 0042 (#806): NULL (None) = the environment's setting; a value only tightens it.
    saves_per_hour: int | None = None
    saves_per_day: int | None = None


async def resolve_plan(db: AsyncSession, ctx: UserContext) -> tuple[Plan, bool]:
    """The effective plan and whether it comes from the stored ``admin`` role."""
    ctx = require_context(ctx)
    admin = await auth.stored_admin(db, ctx.user_id)
    statement, params = (
        (_TOP_PLAN, {}) if admin else (_PLAN, {"owner_id": ctx.user_id, "free": FREE})
    )
    row = (
        await db.execute(statement, params)
    ).one()  # no row means the ``free`` seed is missing: fail loudly, never a default
    return plan_from_row(row), admin


async def effective_plan(db: AsyncSession, ctx: UserContext) -> Plan:
    """The user's effective plan: the top plan for a stored admin, else the highest-ranked active
    entitlement's, ``free`` when none names a plan."""
    return (await resolve_plan(db, ctx))[0]


def plan_from_row(row: Any) -> Plan:
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
        max_class_minutes=row.max_class_minutes,
        games=row.games_allowed,
        races=row.races_allowed,
        ftp_tests=row.ftp_tests_allowed,
        saves_per_hour=row.saves_per_hour,
        saves_per_day=row.saves_per_day,
    )


async def effective_features(db: AsyncSession, ctx: UserContext) -> frozenset[str]:
    """The effective plan's features plus every active entitlement's."""
    ctx = require_context(ctx)
    plan = await effective_plan(db, ctx)
    granted = (await db.execute(_FEATURES, {"owner_id": ctx.user_id})).scalars().all()
    return plan.features | frozenset(granted)
