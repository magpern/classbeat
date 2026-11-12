"""The plan summary of a page of the admin area's Users (ADR 0011 section 6, ADR 0031 section 2).

The effective plan here is ``classbeat.plans.resolve``'s rule written as one set query over the
page: for a stored admin (``classbeat.auth.stored_admins``, one statement for the whole page,
the same rule as ``classbeat.auth.stored_admin``) the highest-ranked plan with source ``admin``
and no end, entitlements or not; for everyone else the highest-ranked plan of the active
entitlements, ``free`` when none, with the source and end of the longest-lasting entitlement
naming it. The features are that plan's plus every active entitlement's, so the role grants no
feature. The class limit is the effective one the ledger enforces
(``classbeat.quota.bounds.effective_classes``: the stored ``classes_per_month`` under
``CLASSBEAT_CLASSES_PER_MONTH_MAX``). The account tables' statements live in ``classbeat.auth``
(guard 8), so the role is read there and passed in as an id array.
"""

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from classbeat import auth
from classbeat.admin.users_models import PlanOut, UserOut
from classbeat.quota.bounds import Bounds, effective_classes

ACTIVE = (
    "e.owner_id = u.id AND e.revoked_at IS NULL AND e.starts_at <= now() "
    "AND (e.ends_at IS NULL OR e.ends_at > now())"
)
MONTH = "date_trunc('month', now() AT TIME ZONE 'UTC')"
# One row per id of ``:ids``: the effective plan and where it comes from, the classes held this
# period (committed, or reserved with a job: ADR 0011 section 3) and this period's model cost.
# ``:admins`` holds the ids of ``:ids`` that are stored admins.
_PLAN_SUMMARY = text(
    "SELECT u.id, p.id AS plan_id, p.name AS plan_name, p.classes_per_month, "  # noqa: S608
    "CASE WHEN who.admin THEN 'admin' ELSE src.source END AS source, src.ends_at, "
    "ARRAY(SELECT f FROM unnest(p.features) AS f UNION SELECT f FROM entitlements e, "
    f"unnest(e.features) AS f WHERE {ACTIVE} ORDER BY 1) AS features, "
    "(SELECT count(*) FROM credit_ledger l WHERE l.owner_id = u.id "
    f"AND l.period = CAST({MONTH} AS date) "
    "AND (l.state = 'committed' OR (l.state = 'reserved' AND l.job_id IS NOT NULL))) AS held, "
    "(SELECT coalesce(sum(r.cost_micros), 0) FROM agent_runs r WHERE r.owner_id = u.id "
    f"AND r.started_at >= {MONTH} AT TIME ZONE 'UTC' "
    f"AND r.started_at < ({MONTH} + interval '1 month') AT TIME ZONE 'UTC') AS cost "
    "FROM unnest(CAST(:ids AS uuid[])) AS u(id) "
    "CROSS JOIN LATERAL (SELECT u.id = ANY(CAST(:admins AS uuid[])) AS admin) who "
    "JOIN LATERAL (SELECT CASE WHEN who.admin "
    "THEN (SELECT q.id FROM plans q ORDER BY q.rank DESC LIMIT 1) "
    "ELSE coalesce((SELECT q.id FROM entitlements e JOIN plans q "
    f"ON q.id = e.plan_id WHERE {ACTIVE} ORDER BY q.rank DESC LIMIT 1), 'free') END AS id) top "
    "ON true JOIN plans p ON p.id = top.id "
    "LEFT JOIN LATERAL (SELECT e.source, e.ends_at FROM entitlements e "
    f"WHERE NOT who.admin AND {ACTIVE} AND e.plan_id = p.id "
    "ORDER BY e.ends_at DESC NULLS FIRST LIMIT 1) src ON true"
)


async def summaries(
    session: AsyncSession, accounts: list[auth.AccountRow], bounds: Bounds
) -> list[UserOut]:
    """``accounts`` with their plan summaries, in order: two statements whatever their number."""
    if not accounts:
        return []
    ids = [a.id for a in accounts]
    admins = await auth.stored_admins(session, ids)
    rows = await session.execute(_PLAN_SUMMARY, {"ids": ids, "admins": list(admins)})
    plans = {row.id: row for row in rows.all()}
    found: list[UserOut] = []
    for a in accounts:
        p = plans[a.id]
        found.append(
            UserOut(
                id=a.id,
                email=a.email,
                created_at=a.created_at,
                role=a.role,
                disabled_at=a.disabled_at,
                plan=PlanOut(id=p.plan_id, name=p.plan_name, source=p.source, ends_at=p.ends_at),
                features=list(p.features),
                classes_held=int(p.held),
                classes_limit=effective_classes(int(p.classes_per_month), bounds),
                cost_micros=int(p.cost),
            )
        )
    return found
