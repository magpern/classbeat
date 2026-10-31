"""The admin area's Usage and cost view and the admin refund (ADR 0011 sections 3 and 6, AD3 #221),
on the router shell of #210.

Every route has ``admin_gate`` (admin role, TOTP within 12 hours, step-up within 5 minutes for the
refund, the admin rate limit, ``no-store``; CSRF by the auth middleware) and runs its database work
in one ``admin_session`` on ``classbeat_maint``: the audit row first, reads read only.

- ``GET /usage?month=YYYY-MM`` (``usage.read``; the current UTC month by default): for the month,
  totals and figures by plan, by kind and model, the completed jobs by kind, the top users by
  cost, the price versions the month's runs were priced with, and the current price table with
  ``PRICES_VERSION`` (read-only, ``classbeat.agent.prices``).
- ``POST /users/{user_id}/credits/{ledger_id}/refund`` (``refund.credit``, step-up): the user's
  ``committed`` ledger row becomes ``refunded`` with ``reason = 'admin_refund'`` and
  ``refunded_by`` the admin, and the period's held count drops by one at once. A row of another
  user or none at all is 404 ``not_found``; a row already ``refunded`` is 409 ``already_refunded``;
  a ``reserved`` or ``released`` row is 409 ``not_committed``, and nothing changes. The row is
  locked (``FOR UPDATE``) and its state read once, so parallel refunds give one 200 and the rest
  409 ``already_refunded``, and a refund racing a settle waits for it and answers from the
  settled state. The ``UPDATE``
  names ``state = 'committed'`` itself, and the ledger trigger refuses maint every other change
  (``credit_ledger_guard``: a live reservation can never be refunded or released by an admin).

**What the figures mean.** Cost, runs and tokens are the ``agent_runs`` rows whose ``started_at``
falls in the month (all kinds; released jobs included: ADR 0011 section 4). **Classes** are the
credit ledger rows of the month's period that are *held*: ``committed``, or ``reserved`` with a
job (section 3); released and refunded rows are counted apart and never as classes. A plan's cost
is that of the runs whose job has a ledger row of that plan (the plan the credit was reserved
under); runs of jobs without a ledger row (revisions, saves, pre-M5 jobs) are ``unattributed``.
Revisions and saves are jobs of those kinds that completed in the month (a deleted class takes its
jobs with it, so a deleted class's jobs no longer count there; its runs still count in cost).

Nothing here reads a job's input, a class's content, an external id or anything of a connection:
maint has no grant on them (guard 11), and nothing here asks.
"""

from collections.abc import Sequence
from datetime import date, datetime
from typing import Annotated, Any, Final
from uuid import UUID

from fastapi import Query, Request
from pydantic import BaseModel, ConfigDict
from sqlalchemy import text

from classbeat import auth
from classbeat.admin.audit import Target
from classbeat.admin.db import AdminDb, AdminRefused, admin_session
from classbeat.admin.routes import AdminActor, admin_db, admin_router
from classbeat.agent.prices import DEFAULT_PRICES, PRICES_VERSION, Price
from classbeat.usage.service import current_month, month_bounds

router = admin_router()

TOP_USERS: Final = 10
REVISION_KINDS: Final = ("revise_draft", "revise_songs")
SAVE_KINDS: Final = ("save_workout", "update_workout")
MONTH_PATTERN: Final = r"^\d{4}-\d{2}$"

_HELD = "(l.state = 'committed' OR (l.state = 'reserved' AND l.job_id IS NOT NULL))"
_WINDOW = "r.started_at >= :start AND r.started_at < :end"
_SUMS = (
    "count(*) AS runs, count(DISTINCT coalesce(r.job_id, r.id)) AS jobs, "
    "coalesce(sum(r.input_tokens), 0) AS input_tokens, "
    "coalesce(sum(r.output_tokens), 0) AS output_tokens, "
    "coalesce(sum(r.cache_read_tokens), 0) AS cache_read_tokens, "
    "coalesce(sum(r.cache_write_tokens), 0) AS cache_write_tokens, "
    "coalesce(sum(r.cost_micros), 0) AS cost_micros"
)
_RUN_TOTALS = text(f"SELECT {_SUMS} FROM agent_runs r WHERE {_WINDOW}")  # noqa: S608
_BY_KIND_MODEL = text(
    f"SELECT r.job_kind, r.provider, r.model, {_SUMS} FROM agent_runs r WHERE {_WINDOW} "  # noqa: S608
    "GROUP BY r.job_kind, r.provider, r.model ORDER BY r.job_kind, r.provider, r.model"
)
_LEDGER_BY_PLAN = text(
    f"SELECT l.plan_id, count(*) FILTER (WHERE {_HELD}) AS classes, "  # noqa: S608
    "count(*) FILTER (WHERE l.state = 'reserved' AND l.job_id IS NOT NULL) AS reserved, "
    "count(*) FILTER (WHERE l.state = 'committed') AS committed, "
    "count(*) FILTER (WHERE l.state = 'released') AS released, "
    "count(*) FILTER (WHERE l.state = 'refunded') AS refunded "
    "FROM credit_ledger l WHERE l.period = :period GROUP BY l.plan_id ORDER BY l.plan_id"
)
_COST_BY_PLAN = text(
    f"SELECT l.plan_id, {_SUMS} FROM agent_runs r JOIN credit_ledger l "  # noqa: S608
    f"ON l.owner_id = r.owner_id AND l.job_id = r.job_id WHERE {_WINDOW} "
    "GROUP BY l.plan_id ORDER BY l.plan_id"
)
_JOBS_BY_KIND = text(
    "SELECT kind, count(*) AS completed FROM jobs "
    "WHERE state = 'completed' AND finished_at >= :start AND finished_at < :end "
    "GROUP BY kind ORDER BY kind"
)
_TOP = text(
    "SELECT r.owner_id, count(*) AS runs, coalesce(sum(r.cost_micros), 0) AS cost_micros "  # noqa: S608
    f"FROM agent_runs r WHERE {_WINDOW} GROUP BY r.owner_id "
    "ORDER BY cost_micros DESC, r.owner_id LIMIT :n"
)
_HELD_BY_OWNER = text(
    "SELECT l.owner_id, count(*) AS classes FROM credit_ledger l "  # noqa: S608
    f"WHERE l.period = :period AND l.owner_id = ANY(CAST(:ids AS uuid[])) AND {_HELD} "
    "GROUP BY l.owner_id"
)
_VERSIONS = text(
    "SELECT r.price_version, count(*) AS runs FROM agent_runs r "  # noqa: S608
    f"WHERE {_WINDOW} GROUP BY r.price_version ORDER BY r.price_version"
)
_REFUND = text(
    "UPDATE credit_ledger SET state = 'refunded', reason = 'admin_refund', refunded_by = :by "
    "WHERE id = :l AND owner_id = :o AND state = 'committed' "
    "RETURNING id, owner_id, job_id, period, plan_id, state, reason, settled_at, refunded_by, "
    "created_at"
)
# The row is locked before anything is decided, so the 409's reason is the state the refund
# itself saw, not a second read after a concurrent settle or refund (#227, PR #281 review).
_LOCK_ROW = text("SELECT state FROM credit_ledger WHERE id = :l AND owner_id = :o FOR UPDATE")
_HELD_IN_PERIOD = text(
    "SELECT count(*) FROM credit_ledger l "  # noqa: S608
    f"WHERE l.owner_id = :o AND l.period = :period AND {_HELD}"
)


class Out(BaseModel):
    model_config = ConfigDict(frozen=True)


class Sums(Out):
    runs: int
    jobs: int
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    cache_write_tokens: int
    cost_micros: int


class Totals(Sums):
    classes: int
    revisions: int
    saves: int


class PlanUsage(Out):
    plan_id: str | None  # None: runs of jobs without a ledger row ("unattributed")
    classes: int
    reserved: int
    committed: int
    released: int
    refunded: int
    sums: Sums


class KindModelUsage(Out):
    job_kind: str
    provider: str
    model: str
    sums: Sums


class KindCount(Out):
    kind: str
    completed: int


class TopUser(Out):
    user_id: UUID
    email: str | None  # None: the account went after the lookup's runs were read
    runs: int
    cost_micros: int
    classes: int


class VersionCount(Out):
    price_version: str
    runs: int


class Rates(Out):
    """Dollars per million tokens, as decimal strings."""

    input: str
    output: str
    cache_write: str
    cache_read: str


class PriceOut(Out):
    provider: str
    model: str
    rates: Rates
    above_tokens: int
    above: Rates | None


class UsageOut(Out):
    month: str
    period: date
    totals: Totals
    by_plan: list[PlanUsage]
    by_kind_model: list[KindModelUsage]
    jobs_by_kind: list[KindCount]
    top_users: list[TopUser]
    price_versions: list[VersionCount]
    prices_version: str
    prices: list[PriceOut]


class RefundOut(Out):
    id: UUID
    user_id: UUID
    job_id: UUID | None
    period: date
    plan_id: str
    state: str
    reason: str | None
    settled_at: datetime | None
    refunded_by: UUID | None
    created_at: datetime
    classes_held: int  # the period's held count after the refund


def _db(request: Request) -> AdminDb:
    db = admin_db(request)
    if db is None:  # the gate answered 503 already; this keeps the type honest
        raise AdminRefused("admin_unavailable", 503)
    return db


_ZERO: Final = {
    "runs": 0,
    "jobs": 0,
    "input_tokens": 0,
    "output_tokens": 0,
    "cache_read_tokens": 0,
    "cache_write_tokens": 0,
    "cost_micros": 0,
}


def _sums(row: Any) -> Sums:
    return Sums(**{name: int(getattr(row, name)) for name in Sums.model_fields})


def _rates(price: Price) -> Rates:
    return Rates(
        input=str(price.input),
        output=str(price.output),
        cache_write=str(price.cache_write),
        cache_read=str(price.cache_read),
    )


def price_table() -> list[PriceOut]:
    """The current price table, read-only (ADR 0011 section 8: a price change stays a reviewed
    code change with a ``PRICES_VERSION`` bump)."""
    found: list[PriceOut] = []
    for provider, model in sorted(DEFAULT_PRICES.priced()):
        price = DEFAULT_PRICES.lookup(provider, model)
        if price is None:  # pragma: no cover - priced() lists only what lookup answers
            continue
        found.append(
            PriceOut(
                provider=provider,
                model=model,
                rates=_rates(price),
                above_tokens=price.above_tokens,
                above=None if price.above is None else _rates(price.above),
            )
        )
    return found


def _by_plan(ledger: Sequence[Any], costs: Sequence[Any], total: Sums) -> list[PlanUsage]:
    cost = {row.plan_id: _sums(row) for row in costs}
    plans: list[PlanUsage] = []
    for row in ledger:
        plans.append(
            PlanUsage(
                plan_id=row.plan_id,
                classes=int(row.classes),
                reserved=int(row.reserved),
                committed=int(row.committed),
                released=int(row.released),
                refunded=int(row.refunded),
                sums=cost.pop(row.plan_id, Sums(**_ZERO)),
            )
        )
    # A plan with runs in this month but no ledger row in its period (a job reserved in the
    # month before): its cost still shows under its plan.
    for plan_id, sums in sorted(cost.items()):
        plans.append(
            PlanUsage(
                plan_id=plan_id, classes=0, reserved=0, committed=0, released=0, refunded=0,
                sums=sums,
            )
        )  # fmt: skip
    attributed = {name: sum(getattr(p.sums, name) for p in plans) for name in _ZERO}
    rest = {name: getattr(total, name) - attributed[name] for name in _ZERO}
    # ``jobs`` and ``runs`` of the rest are exact (a run has one job, a job at most one ledger row).
    if any(rest.values()):
        plans.append(
            PlanUsage(
                plan_id=None, classes=0, reserved=0, committed=0, released=0, refunded=0,
                sums=Sums(**rest),
            )
        )  # fmt: skip
    return plans


@router.get("/usage", response_model=UsageOut)
async def read_usage(
    request: Request,
    actor: AdminActor,
    month: Annotated[str | None, Query(pattern=MONTH_PATTERN)] = None,
) -> UsageOut:
    month = month or current_month()
    bounds = month_bounds(month)
    if bounds is None:
        raise AdminRefused("bad_month", 422)
    start, end = bounds
    period = start.date()
    window = {"start": start, "end": end}
    async with admin_session(_db(request), actor, "usage.read", params={"month": month}) as db:
        total = _sums((await db.execute(_RUN_TOTALS, window)).one())
        ledger = (await db.execute(_LEDGER_BY_PLAN, {"period": period})).all()
        costs = (await db.execute(_COST_BY_PLAN, window)).all()
        kinds = [
            KindModelUsage(job_kind=r.job_kind, provider=r.provider, model=r.model, sums=_sums(r))
            for r in (await db.execute(_BY_KIND_MODEL, window)).all()
        ]
        jobs = [
            KindCount(kind=r.kind, completed=int(r.completed))
            for r in (await db.execute(_JOBS_BY_KIND, window)).all()
        ]
        top = (await db.execute(_TOP, {**window, "n": TOP_USERS})).all()
        ids = [r.owner_id for r in top]
        held = (
            {
                r.owner_id: int(r.classes)
                for r in (await db.execute(_HELD_BY_OWNER, {"period": period, "ids": ids})).all()
            }
            if ids
            else {}
        )
        emails = await auth.account_emails(db, ids)
        versions = [
            VersionCount(price_version=r.price_version, runs=int(r.runs))
            for r in (await db.execute(_VERSIONS, window)).all()
        ]
    by_plan = _by_plan(ledger, costs, total)
    completed = {k.kind: k.completed for k in jobs}
    return UsageOut(
        month=month,
        period=period,
        totals=Totals(
            **total.model_dump(),
            classes=sum(p.classes for p in by_plan),
            revisions=sum(completed.get(k, 0) for k in REVISION_KINDS),
            saves=sum(completed.get(k, 0) for k in SAVE_KINDS),
        ),
        by_plan=by_plan,
        by_kind_model=kinds,
        jobs_by_kind=jobs,
        top_users=[
            TopUser(
                user_id=r.owner_id,
                email=emails.get(r.owner_id),
                runs=int(r.runs),
                cost_micros=int(r.cost_micros),
                classes=held.get(r.owner_id, 0),
            )
            for r in top
        ],
        price_versions=versions,
        prices_version=PRICES_VERSION,
        prices=price_table(),
    )


@router.post("/users/{user_id}/credits/{ledger_id}/refund", response_model=RefundOut)
async def refund_credit(
    request: Request, actor: AdminActor, user_id: UUID, ledger_id: UUID
) -> RefundOut:
    """``committed -> refunded`` (module docstring); 404 for another user's or no row."""
    target = Target(user_id=user_id, kind="credit_ledger", id=str(ledger_id))
    async with admin_session(_db(request), actor, "refund.credit", write=True, target=target) as db:
        ids = {"l": ledger_id, "o": user_id}
        state = (await db.execute(_LOCK_ROW, ids)).scalar_one_or_none()
        if state is None:
            raise AdminRefused("not_found", 404)
        if state != "committed":
            raise AdminRefused("already_refunded" if state == "refunded" else "not_committed", 409)
        # It still names ``state = 'committed'``: the lock holds it; the statement is safe alone.
        row = (await db.execute(_REFUND, {**ids, "by": actor.user_id})).one()
        held = (
            await db.execute(_HELD_IN_PERIOD, {"o": user_id, "period": row.period})
        ).scalar_one()
        return RefundOut(
            id=row.id,
            user_id=row.owner_id,
            job_id=row.job_id,
            period=row.period,
            plan_id=row.plan_id,
            state=row.state,
            reason=row.reason,
            settled_at=row.settled_at,
            refunded_by=row.refunded_by,
            created_at=row.created_at,
            classes_held=int(held),
        )
