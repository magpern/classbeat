"""A composer's swap and change replace the waiting job (ADR 0038 section 6, #1188).

There is one open job per class, a job waiting for approval included (``jobs_one_open_per_class``),
so a swap or a change is never a second job beside the waiting one. It is created through the jobs
API's create with ``ReplaceHold`` (``classbeat.jobs.hold``), as **one** transaction under the owner
lock:

- ``before``: when this request's job exists already (the same idempotency key: a repeated call,
  another tab) nothing is cancelled and the create answers that job. Otherwise the waiting job is
  locked and must still be the one planned: ``waiting_for_approval`` and not past ``expires_at``
  (``expired`` otherwise: a swap or change never starts from an expired list, section 12),
  ``not_waiting`` for any other state; a composer job of ``design_with_songs`` waiting on the
  named list, with the same sketch as its base (``stale_base`` otherwise). Then it is
  **cancelled first** (``jobs.user.cancel``; a composer job holds no credit, so its settle is a
  no-op), so the one-open-job index admits the new one;
- ``refused`` (any refusal of the create: ``revision_limit``, ``quota`` ``cost``, ``pool_busy``
  and every other) raises ``Rollback``: the whole transaction rolls back, the cancel with it, so
  the waiting job and its list stay approvable. The caller answers the refusal and, for a chat
  message, records it on the message in a transaction of its own.

The new job names the job it replaces (``replaces_job_id``), so approving the replaced one is
refused ``superseded`` (``classbeat.jobs.approval``). Nothing here opens a session.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from classbeat.context import UserContext
from classbeat.jobs.hold import Held
from classbeat.jobs.user import cancel
from classbeat.songs.inputs import COMPOSER_MODES

KIND = "design_with_songs"

_BY_KEY = text("SELECT 1 FROM jobs WHERE owner_id = :owner_id AND idempotency_key = :key")
_WAITING = text(
    "SELECT state, kind, class_id, approval_revision_id, input ->> 'mode' AS mode, "
    "input ->> 'base_revision_id' AS base, credit_state, "
    "expires_at IS NOT NULL AND expires_at > now() AS live "
    "FROM jobs WHERE owner_id = :owner_id AND id = :job_id FOR UPDATE"
)
# The class's live composer list waiting for approval: its job, list and sketch (the pending
# submit's state and the swap route read it the same way).
WAITING_SQL = text(
    "SELECT j.id, j.approval_revision_id, j.input FROM jobs j "
    "WHERE j.owner_id = :owner_id AND j.class_id = :class_id AND j.kind = 'design_with_songs' "
    "AND j.state = 'waiting_for_approval' AND j.expires_at > now() "
    "AND j.input ->> 'mode' IN ('fill', 'swap', 'change')"
)


class Rollback(Exception):
    """A refused replace: raised out of the create transaction so the cancel is rolled back."""

    def __init__(self, reason: str, counts: Mapping[str, int | str]) -> None:
        super().__init__(reason)
        self.reason, self.counts = reason, dict(counts)


@dataclass(frozen=True, slots=True)
class ReplaceHold:
    ctx: UserContext
    class_id: UUID
    job_id: UUID  # the waiting job
    list_id: UUID  # the list it waits on
    base: UUID  # its sketch
    key: str  # the new job's idempotency key

    async def before(self, db: AsyncSession, /) -> Held | None:
        owner = {"owner_id": self.ctx.user_id}
        if (await db.execute(_BY_KEY, {**owner, "key": self.key})).first() is not None:
            return None  # this request's job exists: the create answers it
        row = (await db.execute(_WAITING, {**owner, "job_id": self.job_id})).first()
        if row is None or row.class_id != self.class_id:
            return Held("not_found")
        if row.state == "expired" or (row.state == "waiting_for_approval" and not row.live):
            return Held("expired")
        if row.state != "waiting_for_approval":
            return Held("not_waiting")
        if (
            row.kind != KIND
            or row.mode not in COMPOSER_MODES
            or row.approval_revision_id != self.list_id
        ):
            return Held("not_waiting")
        if row.base != str(self.base) or row.credit_state != "none":
            return Held("stale_base")
        await cancel(db, self.ctx, self.job_id)
        return None

    async def refused(
        self, db: AsyncSession, reason: str, counts: Mapping[str, int | str], /
    ) -> Held:
        raise Rollback(reason, counts)


def waiting_parts(raw: Any) -> tuple[UUID, dict[str, Any]] | None:
    """The sketch and the stored input of a waiting composer job's row input, or None."""
    if not isinstance(raw, dict):
        return None
    base = raw.get("base_revision_id")  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
    try:
        return UUID(str(base)), raw  # pyright: ignore[reportUnknownArgumentType]
    except ValueError:
        return None
