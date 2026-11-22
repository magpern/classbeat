"""``classes.save_pending``: the save a composer's Approve and save asked for (ADR 0038 section 7,
#1189; migration 0061).

Phase 2 stores ``{revision_id, content_hash, job_id}`` of the first real draft
(``songs.composer_draft.adopt``) when the approval had ``save: true`` and the fit kept the approved
design. The browser then calls the ordinary save route for **exactly that revision and hash**,
with ``save_pending_job_id`` = ``job_id`` (the phase-2 job); the worker never enqueues it.

- **It counts only while it names the class's current and newest revision** (``current``): any
  newer revision, whether made current or not, voids it in the transaction that inserts it, so a
  revise, a pointer moved back to an older draft or a song change never lets an old save run.
  The class answer shows it through this same rule.
- **The save admit** (``guarded``, around ``save_workout``'s and ``update_workout``'s ``admit``, in
  the create transaction under the owner lock): with ``save_pending_job_id`` present, the class's
  ``save_pending`` (row locked) must be current and name that job, that revision and that hash,
  else ``not_pending``. Without it (the user's own Save or Update) nothing is checked. Either way,
  **an admitted save or update clears it** in that transaction, so the driver and a manual Save
  can never both save: the second is ``class_busy`` while the first is open and ``not_pending``
  after it.
- **The base** (ADR 0042 section 6): with ``base_revision_id``, a class whose current revision is
  another is ``stale_revision`` (``approve_input.stale``), before anything else.
- **A save that ends**, however it ends, finds it already cleared (its admit cleared it, and no
  phase 2 can set it again while the save job is the class's open job), so a failed save shows
  its own error and is never retried by itself.

Every statement filters on ``owner_id`` as well as running under RLS.
"""

from collections.abc import Mapping
from uuid import UUID

from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from classbeat.context import UserContext
from classbeat.jobs.kinds import Admit, Admitted, Refused
from classbeat.saving.approve_input import STALE_REVISION, stale

# The stored value, when the revision it names is the class's current one and no revision of the
# class is newer.
_CURRENT = """
    SELECT c.save_pending FROM classes c
    JOIN class_revisions s ON s.owner_id = c.owner_id AND s.class_id = c.id
        AND s.id = c.current_revision_id
    WHERE c.owner_id = :owner_id AND c.id = :class_id AND c.save_pending IS NOT NULL
    AND c.save_pending ->> 'revision_id' = c.current_revision_id::text
    AND NOT EXISTS (SELECT 1 FROM class_revisions r WHERE r.owner_id = c.owner_id
        AND r.class_id = c.id AND r.number > s.number)
"""
_READ = text(_CURRENT)
_LOCK = text(_CURRENT + " FOR UPDATE OF c")
_CLEAR = text(
    "UPDATE classes SET save_pending = NULL WHERE owner_id = :owner_id AND id = :class_id "
    "AND save_pending IS NOT NULL"
)


class SavePending(BaseModel):
    revision_id: UUID
    content_hash: str
    job_id: UUID


async def current(
    db: AsyncSession, owner_id: UUID, class_id: UUID, *, lock: bool = False
) -> SavePending | None:
    """The class's ``save_pending`` while it counts (module docstring), else None."""
    params = {"owner_id": owner_id, "class_id": class_id}
    raw = (await db.execute(_LOCK if lock else _READ, params)).scalar_one_or_none()
    if not isinstance(raw, Mapping):
        return None
    try:
        return SavePending.model_validate(raw)
    except ValueError:
        return None  # a value this code cannot read counts as none, never guessed at


def guarded(inner: Admit) -> Admit:
    """``inner`` (a save or update kind's ``admit``) with the module docstring's admit rule."""

    async def admit(db: AsyncSession, ctx: UserContext, parsed: BaseModel, /) -> Admitted | Refused:
        class_id = getattr(parsed, "class_id", None)
        job_id = getattr(parsed, "save_pending_job_id", None)
        if not isinstance(class_id, UUID):
            return await inner(db, ctx, parsed)  # the input model refuses it already
        base = getattr(parsed, "base_revision_id", None)
        if await stale(db, ctx.user_id, class_id, base if isinstance(base, UUID) else None):
            return Refused(STALE_REVISION)  # another tab moved the class (ADR 0042 section 6)
        if job_id is not None:
            found = await current(db, ctx.user_id, class_id, lock=True)
            if (
                found is None
                or found.job_id != job_id
                or found.revision_id != getattr(parsed, "revision_id", None)
                or found.content_hash != getattr(parsed, "content_hash", None)
            ):
                return Refused("not_pending")
        outcome = await inner(db, ctx, parsed)
        if isinstance(outcome, Admitted):
            await db.execute(_CLEAR, {"owner_id": ctx.user_id, "class_id": class_id})
        return outcome

    return admit
