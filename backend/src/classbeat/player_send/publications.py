"""The ``player_publications`` rows (ADR 0034 section 5), in the caller's transaction: a job's
fenced session (``JobRun.fenced``) or the class delete's ``user_session``. Every statement names
``owner_id`` and runs under the owner's row security.

- ``planned_number``: the number a send job's attempt uses: its own operation's (a retry keeps
  its number and key) or ``last_number + 1``.
- ``take_number`` (the send's decide step, after the connection hold): the row locked; the
  attempt's number taken as ``last_number + 1``, or accepted again when its operation exists.
- ``removal_still`` (the removal's decide step): the row is still removed with that number.
- ``record_sent`` (``on_succeeded``) and ``move_ahead`` (the player was ahead).
- ``on_class_delete``: the removal a class's deletion enqueues, or the row dropped while the
  player is not connected.
"""

import hashlib
import json
import uuid
from dataclasses import dataclass
from typing import Any, Final
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from classbeat.connections.player_account import player_connected
from classbeat.jobs.events import notify_event, validated_payload
from classbeat.player_publish_policy import NUMBER_LIMIT, REMOVE_KIND, SEND_KIND, valid_number

REMOVE_JOB_KIND: Final = "player_remove"
USER_LEASE_VERSION: Final = 0  # an event written outside any lease (``jobs.user``'s)

_ENSURE = text(
    "INSERT INTO player_publications (owner_id, source_class_id) VALUES (:owner_id, :class_id) "
    "ON CONFLICT (owner_id, source_class_id) DO NOTHING"
)
_LOCK = text(
    "SELECT last_number, removed_at FROM player_publications "
    "WHERE owner_id = :owner_id AND source_class_id = :class_id FOR UPDATE"
)
_LAST = text(
    "SELECT last_number FROM player_publications "
    "WHERE owner_id = :owner_id AND source_class_id = :class_id"
)
_OWN_SEND = text(
    "SELECT request_context ->> 'publication_number' AS number, "
    "request_context ->> 'document_hash' AS document_hash FROM external_operations "
    "WHERE owner_id = :owner_id AND kind = :kind AND class_id = :class_id AND job_id = :job_id "
    "ORDER BY created_at DESC LIMIT 1"
)
_OPERATION = text(
    "SELECT 1 FROM external_operations WHERE owner_id = :owner_id AND kind = :kind "
    "AND idempotency_key = :key"
)
_TAKE = text(
    "UPDATE player_publications SET last_number = :number, updated_at = now() "
    "WHERE owner_id = :owner_id AND source_class_id = :class_id "
    "AND last_number = CAST(:number AS bigint) - 1 AND removed_at IS NULL RETURNING id"
)
_SENT = text(
    "UPDATE player_publications SET sent_revision_id = :revision_id, "
    "sent_document_hash = :document_hash, updated_at = now() "
    "WHERE owner_id = :owner_id AND source_class_id = :class_id AND last_number = :number "
    "AND removed_at IS NULL"
)
_AHEAD = text(
    "UPDATE player_publications SET last_number = :number, updated_at = now() "
    "WHERE owner_id = :owner_id AND source_class_id = :class_id AND last_number < :number"
)
_DROP = text(
    "DELETE FROM player_publications WHERE owner_id = :owner_id AND source_class_id = :class_id"
)
_REMOVED = text(
    "UPDATE player_publications SET last_number = last_number + 1, removed_at = now(), "
    "updated_at = now() WHERE owner_id = :owner_id AND source_class_id = :class_id "
    "RETURNING last_number"
)
_NEW_JOB = text(
    "INSERT INTO jobs (id, owner_id, kind, state, class_id, input, idempotency_key, "
    "request_hash, credit_state, next_seq) VALUES (:id, :owner_id, :kind, 'queued', NULL, "
    "CAST(:input AS jsonb), :key, :request_hash, 'none', 1)"
)
_EVENT = text(
    "INSERT INTO job_events (id, owner_id, job_id, seq, kind, payload, lease_version) "
    "VALUES (:id, :owner_id, :job_id, 1, 'queued', CAST(:payload AS jsonb), :v)"
)
_NOTIFY_QUEUED = text("SELECT pg_notify('jobs_queued', '')")


def _ids(owner_id: UUID, class_id: UUID, **more: Any) -> dict[str, Any]:
    return {"owner_id": owner_id, "class_id": class_id, **more}


@dataclass(frozen=True, slots=True)
class Planned:
    """A send's number and, when its operation exists, the document hash it was taken for."""

    number: int
    document_hash: str | None = None


async def planned_number(db: AsyncSession, owner_id: UUID, class_id: UUID, job_id: UUID) -> Planned:
    own = (
        await db.execute(_OWN_SEND, _ids(owner_id, class_id, kind=SEND_KIND, job_id=job_id))
    ).first()
    if own is not None and own.number is not None and own.number.isdigit():
        return Planned(int(own.number), own.document_hash)
    last = (await db.execute(_LAST, _ids(owner_id, class_id))).scalar_one_or_none()
    return Planned(int(last or 0) + 1)


async def take_number(
    db: AsyncSession, owner_id: UUID, class_id: UUID, number: int, key: str
) -> bool:
    """True when ``number`` is this attempt's: taken now as ``last_number + 1``, or taken by
    this send's operation (``key``) before and not overtaken by a removal."""
    if not valid_number(number):
        return False
    await db.execute(_ENSURE, _ids(owner_id, class_id))
    row = (await db.execute(_LOCK, _ids(owner_id, class_id))).one()
    if row.removed_at is not None:
        return False
    exists = await db.execute(_OPERATION, {"owner_id": owner_id, "kind": SEND_KIND, "key": key})
    if exists.first() is not None:
        return int(row.last_number) >= number
    return (await db.execute(_TAKE, _ids(owner_id, class_id, number=number))).first() is not None


async def removal_still(db: AsyncSession, owner_id: UUID, class_id: UUID, number: int) -> bool:
    row = (await db.execute(_LOCK, _ids(owner_id, class_id))).first()
    return row is not None and row.removed_at is not None and int(row.last_number) == number


async def record_sent(
    db: AsyncSession,
    owner_id: UUID,
    class_id: UUID,
    number: int,
    revision_id: UUID,
    document_hash: bytes,
) -> None:
    """The player holds ``revision_id`` now; not when a later number was taken meanwhile."""
    params = _ids(
        owner_id, class_id, number=number, revision_id=revision_id, document_hash=document_hash
    )
    await db.execute(_SENT, params)


async def move_ahead(db: AsyncSession, owner_id: UUID, class_id: UUID, number: int) -> None:
    """The player's number was ahead of ours: the counter moves to it (never back)."""
    if valid_number(number):
        await db.execute(_AHEAD, _ids(owner_id, class_id, number=number))


async def on_class_delete(db: AsyncSession, owner_id: UUID, class_id: UUID) -> UUID | None:
    """In the class delete's transaction, before the class row goes: a class sent to the player
    and not removed gets a ``player_remove`` job (its number taken, ``removed_at`` set); with the
    player not ``connected`` nothing is sent and the row is dropped. Returns the job's id.

    Lock order: the player connection (share), then the publication row (update), the same
    order as a send's decide step (``hold_player_link``, then ``take_number``), so a delete and
    a send of the class never wait on each other in a cycle. The class row is already locked by
    the delete, and a send job of the class is refused there (``class_busy``)."""
    connected = await player_connected(db, owner_id)
    row = (await db.execute(_LOCK, _ids(owner_id, class_id))).first()
    if row is None or row.removed_at is not None:
        return None
    if not connected or int(row.last_number) + 1 >= NUMBER_LIMIT:
        await db.execute(_DROP, _ids(owner_id, class_id))
        return None
    number = int((await db.execute(_REMOVED, _ids(owner_id, class_id))).scalar_one())
    return await _enqueue_removal(db, owner_id, class_id, number)


async def _enqueue_removal(db: AsyncSession, owner_id: UUID, class_id: UUID, number: int) -> UUID:
    job_id = uuid.uuid4()
    marker = f"{REMOVE_KIND}:{class_id}:{number}".encode()
    await db.execute(
        _NEW_JOB,
        {
            "id": job_id,
            "owner_id": owner_id,
            "kind": REMOVE_JOB_KIND,
            "input": json.dumps({"source_class_id": str(class_id), "number": number}),
            "key": f"player-remove-{job_id.hex}",
            "request_hash": hashlib.sha256(marker).digest(),
        },
    )
    payload = json.dumps(validated_payload("queued", {}))
    await db.execute(
        _EVENT,
        {
            "id": uuid.uuid4(),
            "owner_id": owner_id,
            "job_id": job_id,
            "payload": payload,
            "v": USER_LEASE_VERSION,
        },
    )
    await notify_event(db, job_id)
    await db.execute(_NOTIFY_QUEUED)
    return job_id
