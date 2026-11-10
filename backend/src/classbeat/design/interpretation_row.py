"""``classes.interpretation``: how an ``interpret_request`` read the request, before any design
(ADR 0029 sections 4 and 5; migration 0044).

``{v, job_id, brief, music, items, refusal}``:

- ``job_id``: the ``interpret_request`` job that wrote it. The hand-off derives its idempotency
  key from it (``handoff_key``);
- ``brief`` (the class's stored form) and ``music`` (the design input's, ``{"source": "pick"}`` for
  a song pick, ``None`` for a running class with no MOWL playlist): what the hand-off enqueues,
  unchanged;
- ``items``: the interpretation items (``agent.interpretation_record``, bounded there);
- ``refusal``: the hand-off's stored refusal (a reason code and whole numbers or short codes), so
  every caller after it, a second tab or a reopened one, gets the same answer.

The worker writes it once, in its final fenced transaction, on its own class while the class has
no interpretation and no revision (``store``); after that only the hand-off changes ``refusal``,
under the owner lock (``classbeat.classes.interpretation``). Every statement filters on
``owner_id`` as well as running under RLS. The size is checked here with the jsonb measure, and the
column's ``octet_length`` check is the backstop.
"""

import json
from typing import Annotated, Any, Final, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StringConstraints
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from classbeat.agent.interpretation_record import InterpretationList
from classbeat.jobs import FencedSession, JobFailure
from classbeat.jobs.inputs import jsonb_size

MAX_BYTES: Final = 32768  # the column's check (migration 0044)
Code = Annotated[str, StringConstraints(pattern=r"^[a-z_]{1,40}$")]
CountValue = int | Annotated[str, StringConstraints(max_length=40)]


class Refusal(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: Code
    counts: dict[Code, CountValue] = Field(default_factory=dict[str, CountValue], max_length=8)


class StoredInterpretation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    v: Literal[1] = 1
    job_id: UUID
    brief: dict[str, Any]
    music: dict[str, str] | None
    items: InterpretationList
    refusal: Refusal | None = None

    def dumped(self) -> str:
        value = self.model_dump(mode="json")
        if jsonb_size(value) > MAX_BYTES:
            raise ValueError("the interpretation is too large")
        return json.dumps(value, sort_keys=True, ensure_ascii=False)


def handoff_key(interpret_job_id: UUID) -> str:
    """The design job's idempotency key: one per interpretation, whoever calls the hand-off."""
    return f"interpret-{interpret_job_id.hex}"


# The job's own class, while it has neither an interpretation nor a revision.
_STORE = text(
    "UPDATE classes c SET interpretation = CAST(:v AS jsonb), updated_at = now() FROM jobs j "
    "WHERE j.id = :job_id AND j.owner_id = :owner_id AND c.owner_id = j.owner_id "
    "AND c.id = j.class_id AND c.interpretation IS NULL AND c.current_revision_id IS NULL "
    "RETURNING c.id"
)
_READ = text("SELECT interpretation FROM classes WHERE owner_id = :owner_id AND id = :class_id")
_LOCK = text(
    "SELECT interpretation FROM classes WHERE owner_id = :owner_id AND id = :class_id FOR UPDATE"
)
_WRITE = text(
    "UPDATE classes SET interpretation = CAST(:v AS jsonb), updated_at = now() "
    "WHERE owner_id = :owner_id AND id = :class_id AND interpretation IS NOT NULL"
)


async def store(fs: FencedSession, stored: StoredInterpretation) -> None:
    """In the interpret job's final fenced transaction. Anything else fails the job ``internal``
    (rolled back with the completion)."""
    try:
        value = stored.dumped()
    except ValueError:
        raise JobFailure("invalid_output") from None
    params = {"v": value, "job_id": fs.lease.job_id, "owner_id": fs.lease.owner_id}
    if (await fs.db.execute(_STORE, params)).first() is None:
        raise JobFailure("internal")


def _parse(raw: object) -> StoredInterpretation | None:
    if not isinstance(raw, dict):
        return None
    try:
        return StoredInterpretation.model_validate(raw)
    except ValueError:
        return None  # a row this code cannot read is treated as none, never guessed at


async def read(
    db: AsyncSession, owner_id: UUID, class_id: UUID, *, lock: bool = False
) -> StoredInterpretation | None:
    """The class's interpretation, or None (no class, none stored, or unreadable). ``lock`` takes
    the class row until the transaction ends."""
    params = {"owner_id": owner_id, "class_id": class_id}
    raw = (await db.execute(_LOCK if lock else _READ, params)).scalar_one_or_none()
    return _parse(raw)


async def write(
    db: AsyncSession, owner_id: UUID, class_id: UUID, stored: StoredInterpretation
) -> None:
    """Replace the stored interpretation (the hand-off's refusal, set or cleared)."""
    params = {"v": stored.dumped(), "owner_id": owner_id, "class_id": class_id}
    await db.execute(_WRITE, params)
