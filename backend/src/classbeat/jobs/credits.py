"""The credit seam (ADR 0005 section 5). M5 owns quotas; M3a fixes only the seam.

``reserve`` runs in the create transaction (J3), ``allows`` at approval (J3), ``commit`` and
``release`` in the settle step below. **Settle** runs for each row ``queue_unsettled`` lists, in
that owner's ``user_session``: a conditional ``UPDATE ... WHERE credit_state = 'reserved' AND
state IN (terminal)`` and the ledger call in the same transaction, so a credit settles exactly
once whoever ended the job (worker, reaper, sweep or user). ``completed`` commits; every other
terminal state releases.
"""

import logging
from datetime import date
from typing import Protocol
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from classbeat.context import UserContext
from classbeat.db import user_session
from classbeat.jobs.issuer import owner_context
from classbeat.jobs.queue import unsettled

logger = logging.getLogger(__name__)

Factory = async_sessionmaker[AsyncSession]


class QuotaRefused(Exception):
    """The ledger refuses (ADR 0011 section 3): ``reason`` is ``classes`` (the month's classes
    are held; ``limit`` and ``used`` count classes) or ``cost`` (the month's model cost reached
    the plan's cap; ``limit`` and ``used`` in micro-dollars, never sent to the browser);
    ``period_end`` is the first day of the next UTC month and ``plan`` the effective plan's id.
    Raised inside the transaction, so nothing it would have created survives."""

    def __init__(
        self,
        reason: str = "classes",
        *,
        limit: int | None = None,
        used: int | None = None,
        period_end: date | None = None,
        plan: str | None = None,
    ) -> None:
        super().__init__(reason)
        self.reason = reason
        self.limit = limit
        self.used = used
        self.period_end = period_end
        self.plan = plan

    def detail(self) -> dict[str, int | str]:
        """The 409 ``quota`` body's fields (FE1's contract): ``quota``, and ``limit``, ``used``
        (classes only: a cost refusal shows no money), ``period_end`` (ISO date) and ``plan``
        when known."""
        body: dict[str, int | str] = {"quota": self.reason}
        if self.reason == "classes":
            if self.limit is not None:
                body["limit"] = self.limit
            if self.used is not None:
                body["used"] = self.used
        if self.period_end is not None:
            body["period_end"] = self.period_end.isoformat()
        if self.plan is not None:
            body["plan"] = self.plan
        return body


class CreditLedger(Protocol):
    """``allows`` answers False for a credit it won't let run, or raises ``QuotaRefused`` to
    give the numbers (the metered ledger's cost cap)."""

    async def reserve(self, db: AsyncSession, ctx: UserContext, job_id: UUID) -> None: ...
    async def allows(self, db: AsyncSession, ctx: UserContext, job_id: UUID) -> bool: ...
    async def commit(self, db: AsyncSession, ctx: UserContext, job_id: UUID) -> None: ...
    async def release(self, db: AsyncSession, ctx: UserContext, job_id: UUID) -> None: ...


class UnmeteredLedger:
    """M3a: always allows and writes nothing beyond ``jobs.credit_state``."""

    async def reserve(self, db: AsyncSession, ctx: UserContext, job_id: UUID) -> None:
        return None

    async def allows(self, db: AsyncSession, ctx: UserContext, job_id: UUID) -> bool:
        return True

    async def commit(self, db: AsyncSession, ctx: UserContext, job_id: UUID) -> None:
        return None

    async def release(self, db: AsyncSession, ctx: UserContext, job_id: UUID) -> None:
        return None


_SETTLE = text(
    "UPDATE jobs SET credit_state = CASE WHEN state = 'completed' THEN 'committed' "
    "ELSE 'released' END, updated_at = now() "
    "WHERE id = :job_id AND owner_id = :owner_id AND credit_state = 'reserved' "
    "AND state IN ('completed', 'failed', 'cancelled', 'expired') RETURNING credit_state"
)


async def settle_one(
    factory: Factory, ledger: CreditLedger, owner_id: UUID, job_id: UUID
) -> str | None:
    """Settle one ended job; ``None`` when it was settled already (or is not ended)."""
    ctx = owner_context(owner_id)
    async with user_session(factory, ctx) as db:
        state = (
            await db.execute(_SETTLE, {"job_id": job_id, "owner_id": owner_id})
        ).scalar_one_or_none()
        if state == "committed":
            await ledger.commit(db, ctx, job_id)
        elif state == "released":
            await ledger.release(db, ctx, job_id)
        return state


async def settle(factory: Factory, ledger: CreditLedger, limit: int = 100) -> int:
    """Settle up to ``limit`` ended jobs; a failing one is logged (type only) and skipped."""
    done = 0
    for row in await unsettled(factory, limit):
        try:
            if await settle_one(factory, ledger, row.owner_id, row.job_id) is not None:
                done += 1
        except Exception as exc:  # one bad row must not stop the others
            logger.warning("credit settle failed", extra={"error_type": type(exc).__name__})
    return done
