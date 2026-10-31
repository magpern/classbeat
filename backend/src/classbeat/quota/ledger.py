"""``MeteredLedger``: ADR 0005's ``CreditLedger`` on ``credit_ledger`` (ADR 0011 section 3).

Every method runs in the caller's transaction (``db``, the user's ``user_session``) and names
``owner_id`` in every statement; this module opens no session of its own.

- ``reserve`` (create, after the job row exists): the period's ``quota_periods`` row is created
  if missing and locked (``SELECT ... FOR UPDATE``), so the reservations of one user and month
  run one at a time whatever lock the caller holds. **Held** is then counted: the period's rows
  that are ``committed``, or ``reserved`` with a job (a reservation whose job was deleted with
  its class stops counting). It refuses ``classes`` when held is at the effective
  ``classes_per_month`` and ``cost`` when the period's model cost is at the effective
  ``monthly_cost_micros``, raising ``QuotaRefused`` so the caller's transaction rolls back and
  nothing is created; otherwise it inserts the ``reserved`` row with the period and the plan.
- ``allows`` (approval): the credit is held already, so only the cost cap (``QuotaRefused``
  ``cost``); a row in any state other than ``reserved`` refuses (False). A job without a row
  (created before M5 under ``UnmeteredLedger``) skips that check.
- ``commit`` / ``release`` (settle, in the transaction that sets ``jobs.credit_state``):
  ``reserved`` to ``committed`` with no reason, or to ``released`` with the job's terminal state
  (``failed``, ``cancelled`` or ``expired``) as the reason; ``settled_at = now()``. That is what
  the ledger trigger allows both in ``0008`` and as #276 (``0009_m5_hardening``) tightens it. No
  row (a pre-M5 job) is a no-op, counted in the log.

The plan is ``classbeat.plans.resolve.effective_plan``, read under the lock, and its limits are
``bounds.effective`` (the environment's bounds win).
"""

import logging
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import TextClause

from classbeat.context import UserContext, require_context
from classbeat.jobs.credits import QuotaRefused
from classbeat.plans.resolve import effective_plan
from classbeat.quota import bounds as quota_bounds
from classbeat.quota.bounds import Bounds, effective, next_month, period, period_cost

logger = logging.getLogger(__name__)

_PERIOD_ROW = text(
    "INSERT INTO quota_periods (owner_id, period) VALUES (:o, :p) "
    "ON CONFLICT (owner_id, period) DO NOTHING"
)
_LOCK = text("SELECT id FROM quota_periods WHERE owner_id = :o AND period = :p FOR UPDATE")
_HELD = text(
    "SELECT count(*) FROM credit_ledger WHERE owner_id = :o AND period = :p "
    "AND (state = 'committed' OR (state = 'reserved' AND job_id IS NOT NULL))"
)
_RESERVE = text(
    "INSERT INTO credit_ledger (owner_id, job_id, period, plan_id, state) "
    "VALUES (:o, :j, :p, :plan, 'reserved')"
)
_STATE = text("SELECT state FROM credit_ledger WHERE owner_id = :o AND job_id = :j")
_COMMIT = text(
    "UPDATE credit_ledger SET state = 'committed', reason = NULL, settled_at = now() "
    "WHERE owner_id = :o AND job_id = :j AND state = 'reserved'"
)
# The reason is how the job ended, read from its row in the same statement.
_RELEASE = text(
    "UPDATE credit_ledger l SET state = 'released', reason = j.state, settled_at = now() "
    "FROM jobs j WHERE j.id = l.job_id AND j.owner_id = l.owner_id "
    "AND l.owner_id = :o AND l.job_id = :j AND l.state = 'reserved' "
    "AND j.state IN ('failed', 'cancelled', 'expired')"
)


async def check_cost(db: AsyncSession, ctx: UserContext, limits: Bounds | None = None) -> None:
    """Raise ``QuotaRefused`` ``cost`` when the period's model cost is at the plan's cap (ADR
    0011 section 4): the revise kinds' ``admit`` and the ledger's ``allows``."""
    ctx = require_context(ctx)
    first = await period(db)
    plan = await effective_plan(db, ctx)
    cap = effective(plan, limits or quota_bounds.current()).monthly_cost_micros
    used = await period_cost(db, ctx, first)
    if used >= cap:
        raise QuotaRefused("cost", limit=cap, used=used, period_end=next_month(first), plan=plan.id)


class MeteredLedger:
    """The M5 credit ledger; ``limits`` default to the configured bounds."""

    def __init__(self, limits: Bounds | None = None) -> None:
        self.limits = limits

    def _bounds(self) -> Bounds:
        return self.limits or quota_bounds.current()

    async def reserve(self, db: AsyncSession, ctx: UserContext, job_id: UUID) -> None:
        ctx = require_context(ctx)
        first = await period(db)
        params = {"o": ctx.user_id, "p": first}
        await db.execute(_PERIOD_ROW, params)
        await db.execute(_LOCK, params)  # the reservations of this user and month, one at a time
        held = int((await db.execute(_HELD, params)).scalar_one())
        plan = await effective_plan(db, ctx)
        limits = effective(plan, self._bounds())
        end = next_month(first)
        if held >= limits.classes_per_month:
            raise QuotaRefused(
                "classes", limit=limits.classes_per_month, used=held, period_end=end, plan=plan.id
            )
        used = await period_cost(db, ctx, first)
        if used >= limits.monthly_cost_micros:
            raise QuotaRefused(
                "cost", limit=limits.monthly_cost_micros, used=used, period_end=end, plan=plan.id
            )
        await db.execute(_RESERVE, {**params, "j": job_id, "plan": plan.id})

    async def allows(self, db: AsyncSession, ctx: UserContext, job_id: UUID) -> bool:
        ctx = require_context(ctx)
        state = (await db.execute(_STATE, {"o": ctx.user_id, "j": job_id})).scalar_one_or_none()
        if state is not None and state != "reserved":
            return False
        await check_cost(db, ctx, self._bounds())
        return True

    async def commit(self, db: AsyncSession, ctx: UserContext, job_id: UUID) -> None:
        await self._settle(db, ctx, job_id, _COMMIT, "committed")

    async def release(self, db: AsyncSession, ctx: UserContext, job_id: UUID) -> None:
        await self._settle(db, ctx, job_id, _RELEASE, "released")

    async def _settle(
        self, db: AsyncSession, ctx: UserContext, job_id: UUID, statement: TextClause, to: str
    ) -> None:
        ctx = require_context(ctx)
        result = await db.execute(statement, {"o": ctx.user_id, "j": job_id})
        # No row: a job from before M5 (or settled already), nothing to settle.
        if not result.rowcount:  # pyright: ignore[reportAttributeAccessIssue]
            logger.info("credit settle without a ledger row", extra={"to": to, "rows": 0})
