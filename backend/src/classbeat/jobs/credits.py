"""The credit seam (ADR 0005 section 5). M5 owns quotas; M3a fixes only the seam.

``reserve`` runs in the create transaction (J3), ``allows`` at approval (J3), ``commit`` and
``release`` in the settle step below. **Settle** runs for each row ``queue_unsettled`` lists, in
that owner's ``user_session``: a conditional ``UPDATE ... WHERE credit_state = 'reserved' AND
state IN (terminal)`` and the ledger call in the same transaction, so a credit settles exactly
once whoever ended the job (worker, reaper, sweep or user). ``completed`` commits; every other
terminal state releases.
"""

import logging
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
    """The ledger refuses a reservation (M5)."""


class CreditLedger(Protocol):
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
