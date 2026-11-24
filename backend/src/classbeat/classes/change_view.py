"""Whether a chat change of the class is offered now (``can_change``, ADR 0007 section 3, ADR 0038
amendment of 2026-11-24, #1484): what the history and the class answer both say, from one place.

A class with a stored brief is offered a change when ``design.revise_sketch.can_revise`` admits
it (a sketch through the sketch gate, any other class with a music source through
``music_kind.admit_class``). Reads only, the user's own rows.
"""

from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from classbeat.context import UserContext
from classbeat.design import revise_sketch

_CLASS = text(
    "SELECT brief IS NOT NULL AS briefed, music_source IS NOT NULL AS sourced, current_revision_id "
    "AS base FROM classes WHERE owner_id = :owner_id AND id = :class_id"
)


async def can_change(db: AsyncSession, ctx: UserContext, class_id: UUID) -> bool | None:
    """``None`` for a class that is not the user's; else whether a change is offered now."""
    found = (await db.execute(_CLASS, {"owner_id": ctx.user_id, "class_id": class_id})).first()
    if found is None:
        return None
    return bool(
        found.briefed
        and await revise_sketch.can_revise(db, ctx, class_id, found.base, found.sourced)
    )
