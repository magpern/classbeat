"""Pending chat messages: their storage and their idempotent submit (ADR 0038 section 8, #1186).

Only while ``composer_flow`` is on (``feature_off`` otherwise, after ``not_found`` for a class
that is not the user's). Every statement filters on
``owner_id`` as well as running under RLS; another user's class or message is ``not_found``.

- ``store``: the browser's message ``{message_id, body}`` is stored on the class as ``pending``,
  under the class row's lock. The same id with the same body is the same row (a repeated send);
  with another body it is ``idempotency_mismatch``. A class holds at most one pending message
  (``message_pending`` for a second one; the partial unique index of migration 0060 backs it),
  and at most ``MAX_STORED`` messages of any status (``pending_full`` with ``limit`` and
  ``count``), so store, a refused submit and store again cannot grow without bound; the list
  answers all of them.
- ``submit`` (``classbeat.classes.pending_submit``): the class's state decides what it becomes.
"""

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Final
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from classbeat.context import UserContext, require_context
from classbeat.db import user_session
from classbeat.design import composer

Factory = async_sessionmaker[AsyncSession]
MAX_STORED: Final = 50  # a class's stored messages, of any status
MAX_LISTED: Final = MAX_STORED
MISMATCH: Final = "idempotency_mismatch"

_COLUMNS = "message_id, body, status, reason, job_id, created_at"
CLASS_SQL = text("SELECT 1 FROM classes WHERE owner_id = :owner_id AND id = :class_id")
_EXISTS = text("SELECT 1 FROM classes WHERE owner_id = :owner_id AND id = :class_id FOR UPDATE")
ROW_SQL = text(
    f"SELECT {_COLUMNS} FROM class_pending_messages WHERE owner_id = :owner_id "  # noqa: S608
    "AND class_id = :class_id AND message_id = :message_id"
)
_LIST = text(
    f"SELECT {_COLUMNS} FROM class_pending_messages WHERE owner_id = :owner_id "  # noqa: S608
    "AND class_id = :class_id ORDER BY created_at, id LIMIT :n"
)
_OTHER_PENDING = text(
    "SELECT 1 FROM class_pending_messages WHERE owner_id = :owner_id AND class_id = :class_id "
    "AND status = 'pending'"
)
_COUNT = text(
    "SELECT count(*) FROM class_pending_messages "
    "WHERE owner_id = :owner_id AND class_id = :class_id"
)
_INSERT = text(
    "INSERT INTO class_pending_messages (owner_id, class_id, message_id, body, status) "
    "VALUES (:owner_id, :class_id, :message_id, :body, 'pending')"
)


@dataclass(frozen=True, slots=True)
class Message:
    message_id: UUID
    body: str
    status: str
    reason: str | None
    job_id: UUID | None
    created_at: datetime


@dataclass(frozen=True, slots=True)
class Answer:
    """``message`` when there is one to show; ``reason`` (and ``counts``) when the call refused."""

    message: Message | None = None
    reason: str | None = None
    counts: Mapping[str, int | str] = field(default_factory=dict[str, int | str])
    created: bool = False


def message_of(row: Any) -> Message:
    return Message(row.message_id, row.body, row.status, row.reason, row.job_id, row.created_at)


def ids_of(ctx: UserContext, class_id: UUID, message_id: UUID | None = None) -> dict[str, Any]:
    ids: dict[str, Any] = {"owner_id": ctx.user_id, "class_id": class_id}
    return ids if message_id is None else {**ids, "message_id": message_id}


async def store(
    factory: Factory, ctx: UserContext, class_id: UUID, message_id: UUID, body: str
) -> Answer:
    """Store the message as pending (``created``), or answer the one stored with this id."""
    require_context(ctx)
    async with user_session(factory, ctx) as db:
        if (await db.execute(_EXISTS, ids_of(ctx, class_id))).first() is None:
            return Answer(reason="not_found")  # the class row is locked from here on
        if not await composer.composer_on(db):
            return Answer(reason="feature_off")
        ids = ids_of(ctx, class_id, message_id)
        found = (await db.execute(ROW_SQL, ids)).first()
        if found is not None:
            same = found.body == body
            return Answer(message_of(found)) if same else Answer(reason=MISMATCH)
        if (await db.execute(_OTHER_PENDING, ids_of(ctx, class_id))).first() is not None:
            return Answer(reason="message_pending")
        count = (await db.execute(_COUNT, ids_of(ctx, class_id))).scalar_one()
        if count >= MAX_STORED:
            return Answer(reason="pending_full", counts={"limit": MAX_STORED, "count": count})
        await db.execute(_INSERT, {**ids, "body": body})
        row = (await db.execute(ROW_SQL, ids)).one()
    return Answer(message_of(row), created=True)


async def listed(factory: Factory, ctx: UserContext, class_id: UUID) -> list[Message] | str:
    """The class's stored messages, oldest first, or ``feature_off`` / ``not_found``."""
    require_context(ctx)
    async with user_session(factory, ctx) as db:
        if (await db.execute(CLASS_SQL, ids_of(ctx, class_id))).first() is None:
            return "not_found"
        if not await composer.composer_on(db):
            return "feature_off"
        rows = await db.execute(_LIST, {**ids_of(ctx, class_id), "n": MAX_LISTED})
    return [message_of(r) for r in rows]
