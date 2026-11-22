"""``GET /api/classes/{id}/overlay``: the current draft's edit overlay and the undo and redo steps
(ADR 0042 sections 4 and 6, #1391). Read only, as the user.

- the records, and those without effect: **unapplied** and **orphaned** (``studio_edit_records``);
- **undo** is a pick of the current revision's parent, offered while the current revision is a
  ``user_edit`` or a studio revise (``revise_draft``, #1423: a revise is undone in one step);
  **redo** is a pick of the current revision's **newest** child of those origins. So an undo
  followed by a new edit or revise (a branch) clears the old redo: the newest child is the branch.

A Spotify draft's records name no piece (it has no mix).

Both answer the id and content hash a pick takes (``POST /current`` with ``expected_current_id``,
which answers ``409 stale_revision`` when another tab moved the class).
"""

from dataclasses import dataclass
from typing import Any
from uuid import UUID

from sqlalchemy import TextClause, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from classbeat.classes import spotify_edit_draft
from classbeat.classes.spotify_edit_draft import EDIT_UNAVAILABLE, NO_SPOTIFY_DRAFT
from classbeat.classes.user_edit_model import RecordOut
from classbeat.classes.user_edit_out import records_out
from classbeat.context import UserContext, require_context
from classbeat.db import user_session
from classbeat.design.edit_overlay_model import EditOverlay
from classbeat.studio.mix_proposal_policy import USER_EDIT_ORIGIN
from classbeat.swedebeat_studio import current_draft
from classbeat.swedebeat_studio.studio_edit_records import listed

REVISE_ORIGIN = "revise_draft"  # a studio revise (``swedebeat_studio.revise_kind``)

_UNDO = text(
    "SELECT p.id, p.content_hash FROM class_revisions c JOIN class_revisions p "
    "ON p.owner_id = c.owner_id AND p.class_id = c.class_id AND p.id = c.parent_id "
    "AND p.kind = 'draft' WHERE c.owner_id = :owner_id AND c.class_id = :class_id AND c.id = :id "
    "AND c.provenance ->> 'origin' IN (:origin, :revise)"
)
_REDO = text(
    "SELECT id, content_hash FROM class_revisions WHERE owner_id = :owner_id "
    "AND class_id = :class_id AND parent_id = :id AND kind = 'draft' "
    "AND provenance ->> 'origin' IN (:origin, :revise) ORDER BY number DESC LIMIT 1"
)


@dataclass(frozen=True, slots=True)
class Step:
    revision_id: UUID
    content_hash: str


@dataclass(frozen=True, slots=True)
class _Read:
    """What the view needs of either kind of draft."""

    revision_id: UUID
    content: dict[str, Any]
    overlay: EditOverlay


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
    """The overlay view (module docstring), or ``current_draft.load``'s reason. A class that is no
    studio draft is a Spotify draft with an edit map (#1424), else ``edit_unavailable``."""
    require_context(ctx)
    async with user_session(factory, ctx) as db:
        found = await current_draft.load(db, ctx.user_id, class_id)
        spec: dict[str, Any] = {"tracks": []}  # a Spotify draft has no mix: no piece to name
        analysed: dict[str, str | None] = {}
        if found == "no_studio_draft":
            spotify = await spotify_edit_draft.load(db, ctx.user_id, class_id)
            if isinstance(spotify, str):
                unavailable = spotify in (NO_SPOTIFY_DRAFT, EDIT_UNAVAILABLE)
                return EDIT_UNAVAILABLE if unavailable else spotify
            read = _Read(spotify.revision_id, spotify.content, spotify.overlay)
        elif isinstance(found, str):
            return found
        else:
            spec = found.spec
            analysed = {a.reference: a.sha256 for a in found.music.analyses or ()}
            read = _Read(found.revision_id, found.content, found.overlay)
        unapplied, orphaned = listed(read.content, spec, read.overlay, analysed)
        params: dict[str, object] = {
            "owner_id": ctx.user_id,
            "class_id": class_id,
            "id": read.revision_id,
            "origin": USER_EDIT_ORIGIN,
            "revise": REVISE_ORIGIN,
        }
        undo = await _step(db, _UNDO, params)
        redo = await _step(db, _REDO, params)
    return OverlayView(
        read.revision_id,
        records_out(read.overlay.records, spec),
        records_out(unapplied, spec),
        records_out(orphaned, spec),
        undo,
        redo,
    )
