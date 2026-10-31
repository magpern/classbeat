"""The conversation of a class in SQL (ADR 0007 sections 3 and 4): the create-time reads of
``revise_draft.admit``, the message inserts, and the worker's reads of the base.

A ``class_messages`` row is only ever inserted by ``insert_message``, as ``INSERT ... SELECT``
from the job's own row with ``j.class_id = :class_id``: the schema's foreign key for ``job_id``
checks the owner only, so a message could otherwise name a job of another class. No row means
``MessageRefused``.
"""

import json
import uuid
from dataclasses import dataclass
from typing import Any, Literal
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

_LOCK_CLASS = text(
    "SELECT current_revision_id FROM classes WHERE id = :class_id AND owner_id = :owner_id "
    "FOR UPDATE"
)
_BASE = text(
    "SELECT kind, content_hash FROM class_revisions "
    "WHERE id = :base AND owner_id = :owner_id AND class_id = :class_id"
)
_CHAT_REVISIONS = text(
    "SELECT count(*) FROM class_revisions WHERE owner_id = :owner_id AND class_id = :class_id "
    "AND kind = 'draft' AND provenance ->> 'origin' IN ('revise_draft', 'revise_songs')"
)
_REQUESTS = text(
    "SELECT count(*) FROM class_messages WHERE owner_id = :owner_id AND class_id = :class_id "
    "AND role = 'user'"
)
_INSERT = text(
    "INSERT INTO class_messages (id, owner_id, class_id, seq, role, body, job_id, "
    "base_revision_id, result_revision_id, change) "
    "SELECT :id, j.owner_id, j.class_id, coalesce((SELECT max(m.seq) FROM class_messages m "
    "WHERE m.owner_id = j.owner_id AND m.class_id = j.class_id), 0) + 1, :role, :body, j.id, "
    "CAST(:base AS uuid), CAST(:result AS uuid), CAST(:change AS jsonb) "
    "FROM jobs j WHERE j.id = :job_id AND j.owner_id = :owner_id AND j.class_id = :class_id "
    "RETURNING seq"
)
_BASE_FOR_RUN = text(
    "SELECT j.class_id, c.brief, c.music_source, r.kind, r.content, r.content_hash, "
    "r.music ->> 'fingerprint' AS fingerprint "
    "FROM jobs j JOIN classes c ON c.owner_id = j.owner_id AND c.id = j.class_id "
    "JOIN class_revisions r ON r.owner_id = c.owner_id AND r.class_id = c.id AND r.id = :base "
    "WHERE j.id = :job_id AND j.owner_id = :owner_id"
)
# The requests that made the base and its ancestors, nearest first (at most :n revisions up).
_CHAIN = text(
    "WITH RECURSIVE chain AS ("
    " SELECT r.parent_id, r.provenance ->> 'request_message_id' AS message_id, 1 AS depth "
    " FROM class_revisions r WHERE r.id = :base AND r.owner_id = :owner_id "
    " AND r.class_id = :class_id "
    " UNION ALL "
    " SELECT p.parent_id, p.provenance ->> 'request_message_id', c.depth + 1 "
    " FROM chain c JOIN class_revisions p ON p.id = c.parent_id AND p.owner_id = :owner_id "
    " AND p.class_id = :class_id WHERE c.depth < :n) "
    "SELECT m.body FROM chain c JOIN class_messages m ON m.id = CAST(c.message_id AS uuid) "
    "AND m.owner_id = :owner_id AND m.class_id = :class_id AND m.role = 'user' "
    "ORDER BY c.depth DESC"
)


_REQUEST = text(
    "SELECT body FROM class_messages WHERE owner_id = :owner_id AND class_id = :class_id "
    "AND id = :id AND role = 'user'"
)


class MessageRefused(Exception):
    """The job is not a job of this owner's class: no message was inserted."""


@dataclass(frozen=True, slots=True)
class CreateState:
    """What ``admit`` reads, with the class row locked until the create transaction ends (so a
    pick of the current revision, which locks it first, cannot slip between)."""

    exists: bool
    current: UUID | None
    base_kind: str | None
    base_hash: bytes | None
    chat_revisions: int
    requests: int


async def create_state(db: AsyncSession, owner_id: UUID, class_id: UUID, base: UUID) -> CreateState:
    ids = {"owner_id": owner_id, "class_id": class_id}
    locked = (await db.execute(_LOCK_CLASS, ids)).first()
    if locked is None:
        return CreateState(False, None, None, None, 0, 0)
    found = (await db.execute(_BASE, {**ids, "base": base})).first()
    return CreateState(
        True,
        locked.current_revision_id,
        None if found is None else found.kind,
        None if found is None else bytes(found.content_hash),
        (await db.execute(_CHAT_REVISIONS, ids)).scalar_one(),
        (await db.execute(_REQUESTS, ids)).scalar_one(),
    )


async def insert_message(
    db: AsyncSession,
    *,
    owner_id: UUID,
    class_id: UUID,
    job_id: UUID,
    role: Literal["user", "assistant"],
    body: str,
    base_revision_id: UUID | None,
    result_revision_id: UUID | None = None,
    change: dict[str, Any] | None = None,
    message_id: UUID | None = None,
) -> int:
    """Insert one message of the job's own class; returns its ``seq``."""
    found = await db.execute(
        _INSERT,
        {
            "id": message_id or uuid.uuid4(),
            "owner_id": owner_id,
            "class_id": class_id,
            "job_id": job_id,
            "role": role,
            "body": body,
            "base": base_revision_id,
            "result": result_revision_id,
            "change": None if change is None else json.dumps(change, sort_keys=True),
        },
    )
    row = found.first()
    if row is None:
        raise MessageRefused
    return int(row.seq)


@dataclass(frozen=True, slots=True)
class BaseForRun:
    brief: Any
    music_source: Any
    kind: str
    content: dict[str, Any]
    content_hash: bytes
    fingerprint: str | None
    earlier_requests: tuple[str, ...]


async def load_base(
    db: AsyncSession, owner_id: UUID, job_id: UUID, base: UUID, context: int
) -> BaseForRun | None:
    """The class's brief and music source and the base revision, read through the job's own
    class; and the requests along the base's ``parent_id`` chain, oldest first: at most
    ``context`` revisions up from the base, the base's own request among them."""
    row = (
        await db.execute(_BASE_FOR_RUN, {"owner_id": owner_id, "job_id": job_id, "base": base})
    ).first()
    if row is None:
        return None
    chain = await db.execute(
        _CHAIN, {"owner_id": owner_id, "class_id": row.class_id, "base": base, "n": context}
    )
    return BaseForRun(
        row.brief,
        row.music_source,
        row.kind,
        row.content,
        bytes(row.content_hash),
        row.fingerprint,
        tuple(r.body for r in chain),
    )


async def request_body(
    db: AsyncSession, owner_id: UUID, class_id: UUID, message_id: UUID
) -> str | None:
    """The text of the user's own request message of that class, or None."""
    params = {"owner_id": owner_id, "class_id": class_id, "id": message_id}
    return (await db.execute(_REQUEST, params)).scalar_one_or_none()
