"""The idempotent submit of a pending chat message (ADR 0038 section 8, #1186; split out of
``classbeat.classes.pending``, which stores it).

The class's state decides (``pending_policy``). A queued or running job leaves the message pending
(``class_busy``). A revise (of a sketch or a draft) is created through the jobs API
(``JobEnqueue``) as ``revise_draft`` on the current revision, under the idempotency key of the
class and message id (``submit_key``), with a ``CreateHold``: in the create transaction, under
the owner lock, the message row is locked and must still be pending and the class's state still
the one planned (else the call reads it again, once); a created or repeated job marks it
``submitted`` with its job id in that transaction (``created``); a refusal of the create
(``revision_limit``, ``quota``, ``chat_full`` and every other) marks it ``failed`` with that code
in that transaction. So two concurrent submits make one job, and a refused submit never stays
pending. A composer's song list **waiting** (a live ``fill``, ``swap`` or ``change`` job waiting
for approval, ``song_replace.WAITING_SQL``; any other wait, and one past its expiry, is not this
row) makes it a **change** that replaces the waiting job (``pending_change``, ADR 0038 section
6, #1188); its refusal rolls the replace back and then marks the message ``failed``.
"""

import hashlib
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Final
from uuid import UUID

from pydantic import ValidationError
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from classbeat.classes import pending_change, pending_policy
from classbeat.classes.pending import (
    CLASS_SQL,
    MISMATCH,
    ROW_SQL,
    Answer,
    Factory,
    ids_of,
    message_of,
)
from classbeat.context import UserContext, require_context
from classbeat.db import user_session
from classbeat.design import composer
from classbeat.design.revise import REVISE_DRAFT, ReviseDraftInput
from classbeat.jobs.credits import CreditLedger
from classbeat.jobs.hold import Held
from classbeat.jobs.http import JobEnqueue
from classbeat.jobs.kinds import REASON
from classbeat.jobs.user import JobLimitPolicy, lock_owner

_SUBMIT = text(
    "UPDATE class_pending_messages SET status = 'submitted', job_id = :job_id, "
    "updated_at = now() WHERE owner_id = :owner_id AND class_id = :class_id "
    "AND message_id = :message_id AND status IN ('pending', 'submitted')"
)

_FAIL = text(
    "UPDATE class_pending_messages SET status = 'failed', reason = :reason, updated_at = now() "
    "WHERE owner_id = :owner_id AND class_id = :class_id AND message_id = :message_id "
    "AND status = 'pending'"
)

_STATE = text(
    "SELECT c.current_revision_id, r.kind, r.provenance ->> 'origin' AS origin, "
    "EXISTS (SELECT 1 FROM jobs j WHERE j.owner_id = c.owner_id AND j.class_id = c.id "
    "AND j.state IN ('queued', 'running')) AS running, "
    "EXISTS (SELECT 1 FROM jobs j WHERE j.owner_id = c.owner_id AND j.class_id = c.id "
    "AND j.state = 'waiting_for_approval' AND j.kind = 'design_with_songs' "
    "AND j.expires_at > now() AND j.input ->> 'mode' IN ('fill', 'swap', 'change')) AS waiting "
    "FROM classes c LEFT JOIN class_revisions r ON r.owner_id = c.owner_id AND r.class_id = c.id "
    "AND r.id = c.current_revision_id WHERE c.owner_id = :owner_id AND c.id = :class_id"
)

_JOB_OF_KEY = text(
    "SELECT id FROM jobs WHERE owner_id = :owner_id AND class_id = :class_id "
    "AND idempotency_key = :key"
)

_LOCK_ROW = text(ROW_SQL.text + " FOR UPDATE")

# The hold's own answers: never stored, never shown.
_SUBMITTED: Final = "_submitted"
_DECIDED: Final = "_decided"
_REPLAN: Final = pending_change.REPLAN


def submit_key(class_id: UUID, message_id: UUID) -> str:
    """The job's idempotency key: one per message of a class, whoever submits it. A message id is
    unique only within its class, so the key names both (a digest: the key is at most 64)."""
    digest = hashlib.sha256(class_id.bytes + message_id.bytes).hexdigest()
    return f"pending-{digest[:56]}"


async def _state(db: AsyncSession, ctx: UserContext, class_id: UUID) -> tuple[Any, str] | None:
    row = (await db.execute(_STATE, ids_of(ctx, class_id))).first()
    if row is None:
        return None
    state = pending_policy.ClassState(
        running=bool(row.running),
        waiting=bool(row.waiting),
        current_kind=None if row.current_revision_id is None else row.kind,
        current_origin=row.origin,
    )
    return row.current_revision_id, pending_policy.action(state)


@dataclass(frozen=True, slots=True)
class _Hold:
    """The ``CreateHold`` (with ``created``) of one submit (``classbeat.jobs.hold``)."""

    ctx: UserContext
    class_id: UUID
    message_id: UUID
    base: UUID | None  # the current revision the plan revises
    planned: str  # the policy's action the plan was made for

    def ids(self) -> dict[str, Any]:
        return ids_of(self.ctx, self.class_id, self.message_id)

    async def before(self, db: AsyncSession, /) -> Held | None:
        row = (await db.execute(_LOCK_ROW, self.ids())).first()
        if row is None:
            return Held("not_found")
        if row.status != "pending":
            return Held(_DECIDED)
        now = await _state(db, self.ctx, self.class_id)
        if now is None:
            return Held("not_found")
        current, act = now
        if act == pending_policy.BUSY:
            return Held("class_busy")  # it stays pending: the page submits it after the job
        if act != self.planned or current != self.base:
            return Held(_REPLAN)
        return None

    async def fail(self, db: AsyncSession, reason: str) -> Held:
        code = reason if REASON.match(reason) else "invalid_input"
        await db.execute(_FAIL, {**self.ids(), "reason": code})
        return Held(code)

    async def refused(
        self, db: AsyncSession, reason: str, counts: Mapping[str, int | str], /
    ) -> Held:
        if reason == MISMATCH:
            key = submit_key(self.class_id, self.message_id)
            ids = {"owner_id": self.ctx.user_id, "class_id": self.class_id, "key": key}
            job = (await db.execute(_JOB_OF_KEY, ids)).scalar_one_or_none()
            if job is not None:  # this message's job exists: it is the answer, not a refusal
                await self.created(db, job)
                return Held(_SUBMITTED)
        failed = await self.fail(db, reason)
        return Held(failed.reason, dict(counts))

    async def created(self, db: AsyncSession, job_id: UUID, /) -> None:
        await db.execute(_SUBMIT, {**self.ids(), "job_id": job_id})


async def _decided(factory: Factory, ctx: UserContext, hold: _Hold, reason: str) -> Held:
    """A refusal found before any create (``not_changeable``, ``not_connected``), stored under
    the owner lock like the create's own."""
    async with user_session(factory, ctx) as db:
        await lock_owner(db, ctx)
        early = await hold.before(db)
        if early is not None:
            return early
        return await hold.fail(db, reason)


async def _read(factory: Factory, ctx: UserContext, class_id: UUID, message_id: UUID) -> Answer:
    async with user_session(factory, ctx) as db:
        row = (await db.execute(ROW_SQL, ids_of(ctx, class_id, message_id))).first()
    return Answer(reason="not_found") if row is None else Answer(message_of(row))


async def submit(
    factory: Factory,
    ctx: UserContext,
    class_id: UUID,
    message_id: UUID,
    *,
    ledger: CreditLedger,
    limit: JobLimitPolicy,
    enqueue: JobEnqueue,
) -> Answer:
    """Submit the message as the class's state says. A submitted or failed message is answered
    as it is; ``class_busy`` leaves it pending; a refusal is answered with its code and counts and
    the message is ``failed`` with that code."""
    require_context(ctx)
    for _ in range(2):  # a second plan only when the state changed between the read and the lock
        async with user_session(factory, ctx) as db:
            if (await db.execute(CLASS_SQL, ids_of(ctx, class_id))).first() is None:
                return Answer(reason="not_found")
            if not await composer.composer_on(db):
                return Answer(reason="feature_off")
            row = (await db.execute(ROW_SQL, ids_of(ctx, class_id, message_id))).first()
            now = None if row is None else await _state(db, ctx, class_id)
        if row is None or now is None:
            return Answer(reason="not_found")
        if row.status != "pending":
            return Answer(message_of(row))
        base, act = now
        if act == pending_policy.BUSY:
            return Answer(message_of(row), reason="class_busy")
        hold = _Hold(ctx, class_id, message_id, base, act)
        held = await _create(
            factory, ctx, hold, row.body, ledger=ledger, limit=limit, enqueue=enqueue
        )
        if held is not None and held.reason == _REPLAN:
            continue
        done = await _read(factory, ctx, class_id, message_id)
        if held is None or held.reason in (_DECIDED, _SUBMITTED):
            return Answer(done.message, created=held is None)
        return Answer(done.message, reason=held.reason, counts=dict(held.counts))
    return Answer(reason="class_busy")  # the class kept changing: the page tries again later


async def _create(
    factory: Factory,
    ctx: UserContext,
    hold: _Hold,
    body: str,
    *,
    ledger: CreditLedger,
    limit: JobLimitPolicy,
    enqueue: JobEnqueue,
) -> Held | None:
    """The job of the plan (None: created or repeated), or the hold's answer."""
    if hold.planned == "change":  # a composer's list waits: replace it (#1188)
        return await pending_change.create_change(
            factory,
            ctx,
            hold,
            body,
            key=submit_key(hold.class_id, hold.message_id),
            decided=lambda reason: _decided(factory, ctx, hold, reason),
            ledger=ledger,
            limit=limit,
            enqueue=enqueue,
        )
    if hold.planned not in ("sketch_revise", "revise") or hold.base is None:
        return await _decided(factory, ctx, hold, pending_policy.NOT_CHANGEABLE)
    connection = ctx.connection("mowl")
    if connection is None:
        return await _decided(factory, ctx, hold, "not_connected")
    try:
        parsed = ReviseDraftInput(
            class_id=hold.class_id,
            base_revision_id=hold.base,
            request=body,
            mowl_connection_id=connection,
        )
    except ValidationError:
        return await _decided(factory, ctx, hold, "invalid_input")
    done = await enqueue(
        factory,
        ctx,
        parsed,
        REVISE_DRAFT,
        idempotency_key=submit_key(hold.class_id, hold.message_id),
        ledger=ledger,
        limit=limit,
        consumes_credit=False,
        hold=hold,
    )
    if done.job_id is not None:
        return None
    return Held(done.reason or "invalid_input", dict(done.counts))
