"""A pending chat message submitted while a composer's song list waits: a **change** (ADR 0038
sections 6 and 8, #1188; the table row of ``pending_policy`` that #1186 left as ``not_changeable``).

The message becomes ``design_with_songs`` in mode ``change`` on the waiting list: the same sketch,
every song kept (``seed`` with nothing to replace), the message's words as ``request`` (its chat
message id derived from the pending message's, so a repeated submit is the same request), the
user's current connections, **no credit**. It is created with the pending submit's hold (the
message row locked and still pending, the class still in the planned state; ``submitted`` with the
job in the same transaction) **and** the replace hold (``song_replace.ReplaceHold``: the waiting
job cancelled first). A refusal of the create rolls the whole transaction back, the cancel
included, so the list stays approvable; the caller then marks the message ``failed`` with the
reason in a transaction of its own (``decided``).
"""

import uuid
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Final, Protocol
from uuid import UUID

from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from classbeat.classes.song_replace import WAITING_SQL, ReplaceHold, Rollback, waiting_parts
from classbeat.context import UserContext
from classbeat.db import user_session
from classbeat.jobs.credits import CreditLedger
from classbeat.jobs.hold import Held
from classbeat.jobs.http import JobEnqueue
from classbeat.jobs.user import JobLimitPolicy
from classbeat.songs.inputs import DesignWithSongsInput, Seed
from classbeat.songs.kind import DESIGN_WITH_SONGS

Factory = async_sessionmaker[AsyncSession]
_MESSAGE_NAMESPACE = uuid.UUID("6c1f2b0e-1188-4a6e-9a51-5c0c0a11ce00")
REPLAN: Final = "_replan"  # ``pending_submit``'s own answer: the list moved on, plan again
_MOVED: Final = frozenset({"expired", "not_waiting", "stale_base", "not_found"})


class MessageHold(Protocol):
    @property
    def ctx(self) -> UserContext: ...
    @property
    def class_id(self) -> UUID: ...
    @property
    def message_id(self) -> UUID: ...

    def before(self, db: AsyncSession, /) -> Awaitable[Held | None]: ...
    def created(self, db: AsyncSession, job_id: UUID, /) -> Awaitable[None]: ...


@dataclass(frozen=True, slots=True)
class _Both:
    message: MessageHold
    replace: ReplaceHold

    async def before(self, db: AsyncSession, /) -> Held | None:
        held = await self.message.before(db)
        if held is not None:
            return held
        moved = await self.replace.before(db)
        return Held(REPLAN) if moved is not None and moved.reason in _MOVED else moved

    async def refused(
        self, db: AsyncSession, reason: str, counts: Mapping[str, int | str], /
    ) -> Held:
        raise Rollback(reason, counts)

    async def created(self, db: AsyncSession, job_id: UUID, /) -> None:
        await self.message.created(db, job_id)


def request_message_id(class_id: UUID, message_id: UUID) -> UUID:
    """The change's chat message id: one per pending message of a class."""
    return uuid.uuid5(_MESSAGE_NAMESPACE, f"{class_id}:{message_id}")


async def create_change(
    factory: Factory,
    ctx: UserContext,
    hold: MessageHold,
    body: str,
    *,
    key: str,
    decided: Callable[[str], Awaitable[Held]],
    ledger: CreditLedger,
    limit: JobLimitPolicy,
    enqueue: JobEnqueue,
) -> Held | None:
    """The change of the waiting list (None: created or repeated), or the hold's answer."""
    async with user_session(factory, ctx) as db:
        row = (
            await db.execute(WAITING_SQL, {"owner_id": ctx.user_id, "class_id": hold.class_id})
        ).first()
    parts = None if row is None else waiting_parts(row.input)
    if row is None or parts is None or row.approval_revision_id is None:
        return await decided("not_changeable")
    base, stored = parts
    mowl, spotify = ctx.connection("mowl"), ctx.connection("spotify")
    if mowl is None or spotify is None:
        return await decided("not_connected")
    try:
        parsed = DesignWithSongsInput(
            class_id=hold.class_id,
            brief=stored["brief"],
            mode="change",
            base_revision_id=base,
            replaces_job_id=row.id,
            seed=Seed(song_list_revision_id=row.approval_revision_id, replace=[]),
            sources=stored.get("sources"),
            request=body,
            request_message_id=request_message_id(hold.class_id, hold.message_id),
            mowl_connection_id=mowl,
            spotify_connection_id=spotify,
        )
    except (ValidationError, KeyError):
        return await decided("invalid_input")
    replace = ReplaceHold(ctx, hold.class_id, row.id, row.approval_revision_id, base, key)
    try:
        done = await enqueue(
            factory,
            ctx,
            parsed,
            DESIGN_WITH_SONGS,
            idempotency_key=key,
            ledger=ledger,
            limit=limit,
            consumes_credit=False,  # ADR 0038 section 9
            hold=_Both(hold, replace),
        )
    except Rollback as refused:
        failed = await decided(refused.reason)
        return Held(failed.reason, refused.counts)
    if done.job_id is not None:
        return None
    return Held(done.reason or "invalid_input", dict(done.counts))
