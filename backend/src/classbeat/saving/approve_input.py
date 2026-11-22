"""What a Save and an Update approve (ADR 0007 section 5, ADR 0042 section 6, #1391): one revision
of the class by id and content hash (lower-case hex), and the class's current revision the page
showed (``base_revision_id``, optional). Split out of ``saving.kind`` and ``saving.update``.

``stale`` is the approve's guard against another tab: with ``base_revision_id``, a class whose
current revision is another is refused ``stale_revision`` (``save_pending.guarded``, in the
create transaction, the class row share-locked until it commits). The jobs API's refusal carries
the reason only; the page reloads the class for its current revision.
"""

from typing import Annotated
from uuid import UUID

from pydantic import StringConstraints
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from classbeat.jobs.inputs import JobInput
from classbeat.saving import approval

STALE_REVISION = "stale_revision"
ContentHash = Annotated[str, StringConstraints(pattern=approval.HEX_SHA256)]

_CURRENT = text(
    "SELECT current_revision_id FROM classes WHERE owner_id = :owner_id AND id = :class_id "
    "FOR SHARE"
)


class ApprovedRevision(JobInput):
    revision_id: UUID
    content_hash: ContentHash
    base_revision_id: UUID | None = None  # the current revision the page showed (ADR 0042)


async def stale(db: AsyncSession, owner_id: UUID, class_id: UUID, base: UUID | None) -> bool:
    """Whether ``base`` is given and the class's current revision is not it (no class: stale)."""
    if base is None:
        return False
    params = {"owner_id": owner_id, "class_id": class_id}
    row = (await db.execute(_CURRENT, params)).first()
    return row is None or row.current_revision_id != base
