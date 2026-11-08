"""The admin area's plan edit (ADR 0011 sections 4 and 6; AD4 #222, #496, #809), moved out of
``classbeat.admin.settings``, which still shows the plans in ``GET /settings`` through
``all_plans`` and ``plan_out``.

The route has ``admin_gate`` (admin role, TOTP within 12 hours, step-up within 5 minutes, the
admin rate limit, ``no-store``; CSRF by the auth middleware) and does its work in one
``admin_session`` on ``classbeat_maint``, which writes the ``plan.update`` audit row (a refusal's
too) before anything else. The pure rules are ``classbeat.admin.plan_rules``.

- ``PATCH /plans/{plan_id}`` ``{classes_per_month?, revisions_per_class?, concurrent_jobs?,
  monthly_cost_micros?, max_class_minutes?, games_allowed?, races_allowed?, ftp_tests_allowed?,
  monthly_price_cents?, intro_price_cents?, intro_price_until?, currency?, saves_per_hour?,
  saves_per_day?}``: edits the limits within the table's ranges and under the environment's upper
  bounds (422 ``out_of_range`` or ``above_environment_bound``; ``max_class_minutes`` under
  ``CLASSBEAT_CLASS_MINUTES_MAX``), the feature gates (booleans), the prices (#496) and the plan's
  Save and Update caps (#809). A limit or gate sent as ``null`` is left as it is; a price or a
  save cap sent as ``null`` is cleared. A price is whole cents from 0 to 10 000 000, the
  introduction price's end a date from 2020 to 2100 (``out_of_range``), the currency three capital
  letters (``invalid_value``); an introduction price without its end date (or the reverse), or a
  price without a currency, once merged with the stored row, is 422 ``price_incomplete``. A save
  cap is ``saves_per_hour`` 1 to 1000 or ``saves_per_day`` 1 to 10 000, the table's checks
  (migration 0042; ``out_of_range``); it has no environment bound, because the effective cap is
  the smaller of it and ``CLASSBEAT_SAVES_PER_HOUR`` or ``_PER_DAY``
  (``classbeat.quota.save_caps_policy``), so a larger value is stored and changes nothing. The
  next request reads the new values; credits already held and jobs already created stay as they
  are. Prices are read and returned here only: no route outside ``/api/admin`` reads them (billing
  is off, ADR 0011 section 8).

The audit ``params`` hold the plan's numbers, gates, a currency code and the names cleared, never
free text: a currency that is not three capital letters is left out.
"""

from collections.abc import Sequence
from typing import Annotated, Any
from uuid import UUID

from fastapi import Path, Request
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from classbeat import auth
from classbeat.admin.audit import Target
from classbeat.admin.db import AdminDb, AdminRefused, admin_session
from classbeat.admin.plan_rules import (
    GATES,
    LIMITS,
    NULLABLE,
    bounds,
    plan_params,
    plan_refusal,
    prices_complete,
)
from classbeat.admin.routes import AdminActor, admin_db, admin_router
from classbeat.admin.settings_models import PersonOut, PlanIn, PlanOut
from classbeat.config import Settings

router = admin_router()

PLANS = "/plans"

_PLAN_COLUMNS = (
    "id, name, rank, classes_per_month, revisions_per_class, concurrent_jobs, "
    "monthly_cost_micros, max_class_minutes, games_allowed, races_allowed, ftp_tests_allowed, "
    "monthly_price_cents, intro_price_cents, intro_price_until, currency, saves_per_hour, "
    "saves_per_day, features, updated_at, updated_by"
)
_PLANS = text(f"SELECT {_PLAN_COLUMNS} FROM plans ORDER BY rank")  # noqa: S608
_PLAN_FOR_UPDATE = text(f"SELECT {_PLAN_COLUMNS} FROM plans WHERE id = :p FOR UPDATE")  # noqa: S608
_EDITABLE = frozenset({*LIMITS, *GATES, *NULLABLE})

PlanPath = Annotated[str, Path(pattern=r"^[a-z_]{1,30}$")]


def _update_plan(columns: list[str]) -> Any:
    """``UPDATE plans`` of exactly ``columns``, every name one of ``LIMITS``, ``GATES``,
    ``PRICES`` or ``SAVE_CAPS`` (constants, never the request's text), each bound to the parameter
    of its name."""
    if not columns or not set(columns) <= _EDITABLE:
        raise ValueError("an editable plan column")
    assignments = ", ".join(f"{c} = :{c}" for c in columns)
    return text(
        f"UPDATE plans SET {assignments}, updated_at = now(), updated_by = :by "  # noqa: S608
        f"WHERE id = :p RETURNING {_PLAN_COLUMNS}"
    )


def _db(request: Request) -> AdminDb:
    db = admin_db(request)
    if db is None:  # the gate answered 503 already; this keeps the type honest
        raise AdminRefused("admin_unavailable", 503)
    return db


async def all_plans(session: AsyncSession) -> Sequence[Any]:
    """Every plan row, by rank, with the columns ``plan_out`` shows."""
    return (await session.execute(_PLANS)).all()


def plan_out(row: Any, emails: dict[UUID, str]) -> PlanOut:
    by = row.updated_by
    return PlanOut(
        id=row.id,
        name=row.name,
        rank=row.rank,
        classes_per_month=row.classes_per_month,
        revisions_per_class=row.revisions_per_class,
        concurrent_jobs=row.concurrent_jobs,
        monthly_cost_micros=row.monthly_cost_micros,
        max_class_minutes=row.max_class_minutes,
        games_allowed=row.games_allowed,
        races_allowed=row.races_allowed,
        ftp_tests_allowed=row.ftp_tests_allowed,
        monthly_price_cents=row.monthly_price_cents,
        intro_price_cents=row.intro_price_cents,
        intro_price_until=row.intro_price_until,
        currency=row.currency,
        saves_per_hour=row.saves_per_hour,
        saves_per_day=row.saves_per_day,
        features=list(row.features),
        updated_at=row.updated_at,
        updated_by=None if by is None else PersonOut(id=by, email=emails.get(by)),
    )


@router.patch(f"{PLANS}/{{plan_id}}", response_model=PlanOut)
async def update_plan(
    request: Request, actor: AdminActor, plan_id: PlanPath, body: PlanIn
) -> PlanOut:
    settings: Settings = request.app.state.settings
    asked = body.asked()
    refusal = plan_refusal(asked, bounds(settings))
    async with admin_session(
        _db(request),
        actor,
        "plan.update",
        write=True,
        target=Target(kind="plan", id=plan_id),
        params=plan_params(asked),
    ) as session:
        if refusal is not None:
            raise AdminRefused(refusal, 422)
        current = (await session.execute(_PLAN_FOR_UPDATE, {"p": plan_id})).one_or_none()
        if current is None:
            raise AdminRefused("not_found", 404)
        if not prices_complete({**current._asdict(), **asked}):
            raise AdminRefused("price_incomplete", 422)
        columns = list(asked)
        row = (
            await session.execute(
                _update_plan(columns), {**asked, "p": plan_id, "by": actor.user_id}
            )
        ).one()
        emails = await auth.account_emails(session, [row.updated_by] if row.updated_by else [])
        return plan_out(row, emails)
