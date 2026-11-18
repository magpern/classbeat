"""The user's approval of a waiting job (ADR 0005 section 2, ADR 0008 section 5), split out of
``classbeat.jobs.user`` (CLAUDE.md, split on touch); that module's docstring describes the checks.

A job **replaced** by a composer's swap or change (ADR 0038 section 6, #1188) is ``cancelled``,
and the job that replaced it names it in its input (``replaces_job_id``, a server field): approving
the replaced job is refused ``superseded``, not ``not_waiting``, from the moment of the replace,
before the replacing job has stored a newer list.

``save`` (ADR 0038 section 7, #1189) is stored with the approval as ``approval_save``, the
receipt's part a resume reads; the jobs API's ``admit`` refuses it for a job whose kind does not
save on approval (``JobKind.saves_on_approval``). A repeated approval is the no-op only with the
same ``save``: one that asks otherwise is ``not_waiting``, never a silently dropped save.
"""

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from classbeat.context import UserContext, require_context
from classbeat.jobs.user import LOCK_SQL, NOTIFY_QUEUED, lock_owner, user_event

_NEWEST_LIST = text(
    "SELECT id FROM class_revisions WHERE owner_id = :owner_id AND class_id = :class_id "
    "AND kind = 'song_list' ORDER BY number DESC LIMIT 1"
)
# The approved revision must be one of the user's own song lists of the job's class: anything else
# (another user's, another class's, an unknown id) is answered like an unknown job (ADR 0008
# section 12: a foreign revision is 404, never "superseded").
_OWN_LIST = text(
    "SELECT 1 FROM class_revisions WHERE owner_id = :owner_id AND class_id = :class_id "
    "AND id = :revision_id AND kind = 'song_list'"
)
_APPROVE = text(
    "UPDATE jobs SET state = 'queued', attempt = 0, approved_at = now(), available_at = now(), "
    "approval_save = :save, "
    "next_seq = next_seq + 1, updated_at = now() "
    "WHERE id = :job_id AND owner_id = :owner_id AND state = 'waiting_for_approval' "
    "RETURNING next_seq"
)
# A job of the class that replaced this one (a composer's swap or change; ADR 0038 section 6).
_REPLACED = text(
    "SELECT 1 FROM jobs WHERE owner_id = :owner_id AND class_id = :class_id "
    "AND input ->> 'replaces_job_id' = :job_text LIMIT 1"
)


@dataclass(frozen=True, slots=True)
class Waiting:
    """What ``admit`` sees of a job that passed the row checks of an approval."""

    job_id: UUID
    kind: str
    input: Mapping[str, Any]
    credit_state: str  # the job row's: 'reserved' when it consumes a credit, else 'none'
    class_id: UUID
    revision_id: UUID  # the job's approval revision, the class's newest song list
    content_hash: bytes  # the approved hash, equal to the job's approval_hash


# The jobs API's checks at approval (connections, per-user limit, credit): a refusal reason, or
# None to accept. Runs in the approval's transaction, after the owner lock and the row lock.
Admit = Callable[[AsyncSession, Waiting], Awaitable[str | None]]


@dataclass(frozen=True, slots=True)
class Approval:
    accepted: bool
    state: str | None
    # not_found (also for a revision that is not one of the user's song lists of the job's
    # class), not_waiting, expired, superseded, hash_mismatch, or the reason ``admit`` gave
    reason: str | None = None


async def approve(
    db: AsyncSession,
    ctx: UserContext,
    job_id: UUID,
    revision_id: UUID,
    content_hash: bytes,
    *,
    admit: Admit,
    save: bool = False,
) -> Approval:
    """``admit`` is required (#115 item 7): it holds the connection, per-user limit and credit
    checks, and leaving it out would skip them."""
    require_context(ctx)
    params = {"job_id": job_id, "owner_id": ctx.user_id}
    await lock_owner(db, ctx)  # before anything is counted (ADR 0005 section 2, #87)
    row = (await db.execute(LOCK_SQL, params)).first()
    if row is None:
        return Approval(False, None, "not_found")
    same = row.approval_revision_id == revision_id and (
        row.approval_hash is not None and bytes(row.approval_hash) == content_hash
    )
    if row.state != "waiting_for_approval":
        if row.approved_at is not None and same and bool(row.approval_save) == save:
            return Approval(True, row.state)  # a repeated approval: no second event
        if row.state == "expired":  # the sweep got there first: same reason as a late approval
            return Approval(False, row.state, "expired")
        if row.state == "cancelled" and await _replaced(db, ctx, row.class_id, job_id):
            return Approval(False, row.state, "superseded")  # a swap or change replaced it
        return Approval(False, row.state, "not_waiting")
    if not row.live:
        return Approval(False, row.state, "expired")
    newest = (
        await db.execute(_NEWEST_LIST, {"owner_id": ctx.user_id, "class_id": row.class_id})
    ).scalar_one_or_none()
    if row.approval_revision_id != revision_id:
        own = await db.execute(
            _OWN_LIST,
            {"owner_id": ctx.user_id, "class_id": row.class_id, "revision_id": revision_id},
        )
        if own.first() is None:
            return Approval(False, row.state, "not_found")
    if row.approval_revision_id != revision_id or newest != revision_id:
        return Approval(False, row.state, "superseded")
    if not same:
        return Approval(False, row.state, "hash_mismatch")
    reason = await admit(
        db,
        Waiting(
            job_id, row.kind, row.input, row.credit_state, row.class_id, revision_id, content_hash
        ),
    )
    if reason is not None:
        return Approval(False, row.state, reason)
    seq = (await db.execute(_APPROVE, {**params, "save": save})).scalar_one()
    await user_event(db, ctx, job_id, seq, "approved")
    await db.execute(NOTIFY_QUEUED)
    return Approval(True, "queued")


async def _replaced(db: AsyncSession, ctx: UserContext, class_id: UUID, job_id: UUID) -> bool:
    params = {"owner_id": ctx.user_id, "class_id": class_id, "job_text": str(job_id)}
    return (await db.execute(_REPLACED, params)).first() is not None
