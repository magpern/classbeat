"""``GET /api/classes/{id}/overlay``: the current draft's edit overlay and the undo and redo steps
(ADR 0042 sections 4 and 6, #1391). Read only, as the user.

- the records, and those without effect: **unapplied** and **orphaned** (``studio_edit_records``);
- **undo** is a pick of the current revision's parent, offered while the current revision is a
  ``user_edit``; **redo** is a pick of the current revision's **newest** ``user_edit`` child. So an
  undo followed by a new edit (a branch) clears the old redo: the newest child is the branch.

Both answer the id and content hash a pick takes (``POST /current`` with ``expected_current_id``,
which answers ``409 stale_revision`` when another tab moved the class).
"""

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import TextClause, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from classbeat.classes.user_edit_model import RecordOut
from classbeat.classes.user_edit_out import records_out
from classbeat.context import UserContext, require_context
from classbeat.db import user_session
from classbeat.studio.mix_proposal_policy import USER_EDIT_ORIGIN
from classbeat.swedebeat_studio import current_draft
from classbeat.swedebeat_studio.studio_edit_records import listed

_UNDO = text(
    "SELECT p.id, p.content_hash FROM class_revisions c JOIN class_revisions p "
    "ON p.owner_id = c.owner_id AND p.class_id = c.class_id AND p.id = c.parent_id "
    "AND p.kind = 'draft' WHERE c.owner_id = :owner_id AND c.class_id = :class_id AND c.id = :id "
    "AND c.provenance ->> 'origin' = :origin"
)
_REDO = text(
    "SELECT id, content_hash FROM class_revisions WHERE owner_id = :owner_id "
    "AND class_id = :class_id AND parent_id = :id AND kind = 'draft' "
    "AND provenance ->> 'origin' = :origin ORDER BY number DESC LIMIT 1"
)


@dataclass(frozen=True, slots=True)
class Step:
    revision_id: UUID
    content_hash: str


@dataclass(frozen=True, slots=True)
class OverlayView:
    revision_id: UUID
    records: list[RecordOut]
    unapplied: list[RecordOut]
    orphaned: list[RecordOut]
    undo: Step | None
    redo: Step | None


async def _step(db: AsyncSession, sql: TextClause, params: dict[str, object]) -> Step | None:
    row = (await db.execute(sql, params)).first()
    return None if row is None else Step(row.id, bytes(row.content_hash).hex())


async def view(
    factory: async_sessionmaker[AsyncSession], ctx: UserContext, class_id: UUID
) -> OverlayView | str:
    """The overlay view (module docstring), or ``current_draft.load``'s reason
    (``no_studio_draft`` is ``edit_unavailable``)."""
    require_context(ctx)
    async with user_session(factory, ctx) as db:
        found = await current_draft.load(db, ctx.user_id, class_id)
        if isinstance(found, str):
            return "edit_unavailable" if found == "no_studio_draft" else found
        analysed = {a.reference: a.sha256 for a in found.music.analyses or ()}
        unapplied, orphaned = listed(found.content, found.spec, found.overlay, analysed)
        params: dict[str, object] = {
            "owner_id": ctx.user_id,
            "class_id": class_id,
            "id": found.revision_id,
            "origin": USER_EDIT_ORIGIN,
        }
        undo = await _step(db, _UNDO, params)
        redo = await _step(db, _REDO, params)
    spec = found.spec
    return OverlayView(
        found.revision_id,
        records_out(found.overlay.records, spec),
        records_out(unapplied, spec),
        records_out(orphaned, spec),
        undo,
        redo,
    )
