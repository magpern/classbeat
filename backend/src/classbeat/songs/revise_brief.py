"""The brief a Change songs (``revise_songs``) phase one searches with, split out of
``classbeat.songs.revise`` (ADR 0008 section 6; ADR 0029 section 4, #904).

- ``class_brief``: the class's stored brief with the stated layer nearest the base applied
  (``design.chat.stated_layer``), as phase two reads it through ``chat.load_base``, so both
  phases of one job size and check the class by the same length and stated values;
- ``pick_brief``: that brief with the request first in its free text, within the brief's 2000
  characters.
"""

from typing import Any, Final
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from classbeat.agent import Brief
from classbeat.agent.stated_layer import applied
from classbeat.design import chat

MAX_BRIEF_TEXT: Final = 2000

_CLASS_BRIEF = text(
    "SELECT c.id AS class_id, c.brief FROM jobs j JOIN classes c ON c.owner_id = j.owner_id "
    "AND c.id = j.class_id WHERE j.id = :job_id AND j.owner_id = :owner_id"
)


async def class_brief(
    db: AsyncSession, owner_id: UUID, job_id: UUID, base: UUID
) -> dict[str, Any] | None:
    """The job's class's brief as of ``base``, or None (no class, or no brief)."""
    row = (await db.execute(_CLASS_BRIEF, {"job_id": job_id, "owner_id": owner_id})).first()
    if row is None or not isinstance(row.brief, dict):
        return None
    return applied(row.brief, await chat.stated_layer(db, owner_id, row.class_id, base))  # pyright: ignore[reportUnknownArgumentType]


def pick_brief(brief: Brief, request: str) -> Brief:
    """The brief phase 1 searches with: the request first, then the brief's own text, within the
    brief's 2000 characters (both are already clean text)."""
    joined = f"{request}\n\n{brief.text}" if brief.text else request
    return brief.model_copy(update={"text": joined[:MAX_BRIEF_TEXT].rstrip()})
