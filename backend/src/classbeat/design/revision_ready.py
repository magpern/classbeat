"""``revision_ready``: the one server-side rule that keeps a sketch from being written (ADR 0038
section 4, #1186).

Every route and job that takes a revision id calls ``revision_ready`` with that id before it does
anything else with the revision, and so before any MOWL, Spotify or player call: ``save_workout``,
``update_workout`` and ``send_to_player`` (through ``classbeat.saving.approval.revision``),
``duplicate``, ``POST /api/classes/{id}/current``, the beat-one correction, ``refit_draft``,
``revise_songs`` and ``studio_revise``. A revision whose origin is not ready
(``revision_ready_policy``: ``sketch``) answers the closed code ``not_ready``. A revision the
caller's class does not have answers None, so each caller keeps its own ``not_found`` or
``invalid_input`` for it. Revisions are immutable (ADR 0007 section 2), so the origin read at
creation is the origin for good: no second check in the worker is needed.

``tests/test_revision_ready_guard.py`` lists every route and job kind that takes a revision id and
fails when one does not reach this call and is not on its reviewed allowlist.
"""

from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from classbeat.design.revision_ready_policy import NOT_READY as NOT_READY
from classbeat.design.revision_ready_policy import SKETCH, refusal

_ORIGIN = text(
    "SELECT provenance ->> 'origin' AS origin FROM class_revisions "
    "WHERE owner_id = :owner_id AND class_id = :class_id AND id = :revision_id"
)


async def revision_ready(
    db: AsyncSession, owner_id: UUID, class_id: UUID, revision_id: UUID
) -> str | None:
    """``not_ready`` for a revision of the class whose origin is not ready; None otherwise (a ready
    revision, or none of this owner's class by that id). Reads only."""
    params = {"owner_id": owner_id, "class_id": class_id, "revision_id": revision_id}
    row = (await db.execute(_ORIGIN, params)).first()
    return None if row is None else refusal(row.origin)


async def is_sketch(db: AsyncSession, owner_id: UUID, class_id: UUID, revision_id: UUID) -> bool:
    """Whether the revision of the class is a sketch: ``revise_draft`` takes one as its base on
    purpose (ADR 0038 section 4) and revises it as a sketch. Reads only."""
    params = {"owner_id": owner_id, "class_id": class_id, "revision_id": revision_id}
    row = (await db.execute(_ORIGIN, params)).first()
    return row is not None and row.origin == SKETCH
