"""Update's target (ADR 0007 section 7), split out of ``classbeat.saving.approval``: the user's
own **live** saved row of the class an Update writes, the only place its program id is read from.
"""

from dataclasses import dataclass
from typing import Final
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from classbeat.context import UserContext

MAX_PROGRAM_ID: Final = 2**63 - 1

# The user's own **live** saved row **of this class**, and
# its creating operation, when it still names one, of the same class too (the foreign key is
# owner-inclusive, not class-inclusive: #156 review). The program id is read only from here.
_SAVED_TARGET = text(
    "SELECT s.id, s.program_id, s.state_hash FROM saved_workouts s "
    "WHERE s.owner_id = :owner_id AND s.id = :saved_workout_id AND s.class_id = :class_id "
    "AND s.detached_at IS NULL "
    "AND (s.operation_id IS NULL OR EXISTS (SELECT 1 FROM external_operations o "
    "WHERE o.owner_id = s.owner_id AND o.id = s.operation_id AND o.class_id = s.class_id))"
)


@dataclass(frozen=True, slots=True)
class SavedTarget:
    """The program an Update may write, from the user's own live saved row."""

    saved_workout_id: UUID
    program_id: int
    state_hash: bytes | None


async def saved_target(
    db: AsyncSession, ctx: UserContext, class_id: UUID, saved_workout_id: UUID
) -> SavedTarget | None:
    """The live saved row ``saved_workout_id`` of the user's class ``class_id``, else None (another
    user's, another class's, a detached or an unknown row: all the same answer)."""
    row = (
        await db.execute(
            _SAVED_TARGET,
            {
                "owner_id": ctx.user_id,
                "saved_workout_id": saved_workout_id,
                "class_id": class_id,
            },
        )
    ).first()
    if row is None:
        return None
    program_id = row.program_id
    if not isinstance(program_id, int) or not 0 < program_id <= MAX_PROGRAM_ID:
        return None
    state = None if row.state_hash is None else bytes(row.state_hash)
    return SavedTarget(row.id, program_id, state)
