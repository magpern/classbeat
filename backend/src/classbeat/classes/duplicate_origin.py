"""The provenance a duplicate describes (ADR 0007 amendment of 2026-11-03, part D; #1424).

A ``user_edit`` revision (ADR 0042 section 4) ran no model, so its provenance names none, and a
copy's ``provenance_copy`` needs the descriptive fields of how the content was **made**. So a copy
of an edited draft describes the nearest ancestor that is not a ``user_edit`` (the draft the user
edited); any other revision describes itself. An ancestor that is gone is the revision's own
provenance, which then fails ``provenance_copy``'s validation: the duplicate is refused, never
guessed.
"""

from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from classbeat.studio.mix_proposal_policy import USER_EDIT_ORIGIN
from classbeat.swedebeat_studio import mix_proposal

_PROVENANCE = text(
    "SELECT provenance FROM class_revisions WHERE owner_id = :owner_id AND class_id = :class_id "
    "AND id = :id AND kind = 'draft'"
)


async def described(
    db: AsyncSession, owner_id: UUID, class_id: UUID, revision_id: UUID, provenance: Any
) -> Any:
    """``provenance`` of the revision, or its nearest non-``user_edit`` ancestor's (module
    docstring)."""
    if not isinstance(provenance, dict) or provenance.get("origin") != USER_EDIT_ORIGIN:  # pyright: ignore[reportUnknownMemberType]
        return provenance
    at = await mix_proposal.not_user_edit(db, owner_id, class_id, revision_id)
    if at is None:
        return provenance
    row = (
        await db.execute(_PROVENANCE, {"owner_id": owner_id, "class_id": class_id, "id": at})
    ).first()
    return provenance if row is None else row.provenance
