"""The one place the app role adds an entitlement (ADR 0011 sections 2 and 4).

The schema allows an entitlement of any plan with no end; the app must not. Every row added here
has ``starts_at = now()`` and ``ends_at = now() + days`` (1 to 366 days, so it always ends), and:

- ``code``: carries its ``code_id`` (the code's own plan, features and ``grant_days``);
- ``trial``: names a plan that ranks at most ``instructor``. The caller clamps the stored setting
  first (``classbeat.plans.trial``); this refuses anything that got past it.

Grants (``source = 'grant'``) are the admin's, through ``classbeat_maint``; the row policy refuses
them for the app role anyway. Runs inside the caller's ``user_session``; opens none.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Literal
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from classbeat.context import UserContext, require_context

TRIAL_MAX_PLAN = "instructor"
MAX_DAYS = 366

_RANKS = text("SELECT id, rank FROM plans WHERE id = ANY(:ids)")
_COLUMNS = (
    "INSERT INTO entitlements (owner_id, plan_id, features, source, starts_at, ends_at, code_id) "
    "VALUES (:owner_id, :plan_id, :features, :source, now(), "
    "now() + make_interval(days => :days), :code_id) "
)
_RETURNING = "RETURNING id, plan_id, features, ends_at"
_INSERT = text(_COLUMNS + _RETURNING)
# One trial per account, ever (the partial unique index): a second one inserts nothing.
_INSERT_TRIAL = text(
    _COLUMNS + "ON CONFLICT (owner_id) WHERE source = 'trial' DO NOTHING " + _RETURNING
)


async def plan_ranks(db: AsyncSession, *ids: str) -> dict[str, int]:
    """The rank of each of ``ids`` that is a plan."""
    rows = (await db.execute(_RANKS, {"ids": list(ids)})).all()
    return {row.id: row.rank for row in rows}


class TermsRefused(ValueError):
    """The entitlement asked for breaks ADR 0011's terms; nothing was written."""


@dataclass(frozen=True, slots=True)
class Added:
    id: UUID
    plan_id: str | None
    features: tuple[str, ...]
    ends_at: datetime


async def add_entitlement(
    db: AsyncSession,
    ctx: UserContext,
    *,
    source: Literal["code", "trial"],
    plan_id: str | None,
    features: tuple[str, ...] = (),
    days: int,
    code_id: UUID | None = None,
) -> Added | None:
    """Insert the entitlement; ``None`` only for a trial when the account already had one."""
    ctx = require_context(ctx)
    if type(days) is not int or not 1 <= days <= MAX_DAYS:
        raise TermsRefused("an entitlement from a code or trial ends within 1 to 366 days")
    if plan_id is None and not features:
        raise TermsRefused("an entitlement grants a plan or a feature")
    if source == "code":
        if code_id is None:
            raise TermsRefused("a code entitlement names its code")
    elif source == "trial":
        if plan_id is None or code_id is not None or features:
            raise TermsRefused("a trial is a plan only")
        ranks = await plan_ranks(db, plan_id, TRIAL_MAX_PLAN)
        if plan_id not in ranks or TRIAL_MAX_PLAN not in ranks:
            raise TermsRefused("unknown trial plan")
        if ranks[plan_id] > ranks[TRIAL_MAX_PLAN]:
            raise TermsRefused("a trial ranks at most instructor")
    else:
        raise TermsRefused("the app adds only code and trial entitlements")
    params = {
        "owner_id": ctx.user_id,
        "plan_id": plan_id,
        "features": list(features),
        "source": source,
        "days": days,
        "code_id": code_id,
    }
    row = (await db.execute(_INSERT_TRIAL if source == "trial" else _INSERT, params)).one_or_none()
    if row is None:
        return None
    return Added(row.id, row.plan_id, tuple(row.features), row.ends_at)
