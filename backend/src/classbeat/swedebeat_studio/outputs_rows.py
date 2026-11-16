"""The ``class_outputs`` rows of a studio class (migration 0054; ADR 0036 section 6, SR C7).

One row per output **attempt** of one approved draft (``attempt`` from 1), unique on ``(owner_id,
class_id, revision_id, output, attempt)``. Every statement filters on ``owner_id`` itself as well
as through row security; the worker writes through its fence (``fs.db``), the web through a
``user_session`` of the user (``outputs_view``).

- ``latest``: an output's newest attempt of a draft (locked ``FOR UPDATE`` when asked).
- ``open_attempt``: insert attempt ``n`` with what it must keep **before the first write intent**
  (the names, the body, the render plan and the analysed sha256s: ``detail``), after the limits
  (``check_limits``): at most ``ATTEMPTS_PER_CLASS`` attempts of a class and ``STARTS_PER_DAY``
  per user in any 24 hours (``studio_output_starts``, the ``mixlimit`` pattern: a per-user
  advisory lock, a row per attempt, kept through a class delete), else ``output_limit`` and
  nothing written. A racing insert of the same attempt is not an error: the existing row is used.
- ``update``: the state, the external id and a patch of ``detail`` (merged, never replaced).
- ``operations``: each step's operation state of one attempt, and whether a running job holds it.
"""

import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Final
from uuid import UUID

from sqlalchemy import bindparam, text
from sqlalchemy.ext.asyncio import AsyncSession

from classbeat.jobs import JobFailure
from classbeat.studio.output_key_policy import STEPS, key

ATTEMPTS_PER_CLASS: Final = 24  # four outputs, a few "Create another anyway" each
STARTS_PER_DAY: Final = 24
KEEP_HOURS: Final = 48
LIMIT_CATEGORY: Final = "output_limit"

_COLUMNS = "id, class_id, revision_id, output, attempt, state, external_id, detail, updated_at"
_LATEST = (
    f"SELECT {_COLUMNS} FROM class_outputs"  # noqa: S608 - constant column list
    "  WHERE owner_id = :owner_id AND class_id = :class_id "
    "AND revision_id = :revision_id AND output = :output ORDER BY attempt DESC LIMIT 1"
)
_LATEST_READ = text(_LATEST)
_LATEST_LOCKED = text(_LATEST + " FOR UPDATE")
_ALL_LATEST = text(
    f"SELECT DISTINCT ON (revision_id, output) {_COLUMNS} FROM class_outputs "  # noqa: S608
    "WHERE owner_id = :owner_id AND class_id = :class_id "
    "ORDER BY revision_id, output, attempt DESC"
)
_INSERT = text(
    "INSERT INTO class_outputs (owner_id, class_id, revision_id, output, attempt, state, detail) "
    "VALUES (:owner_id, :class_id, :revision_id, :output, :attempt, 'pending', "
    "CAST(:detail AS jsonb)) ON CONFLICT (owner_id, class_id, revision_id, output, attempt) "
    "DO NOTHING RETURNING id"
)
_UPDATE = text(
    "UPDATE class_outputs SET state = coalesce(:state, state), "
    "external_id = coalesce(:external_id, external_id), "
    "detail = detail || CAST(:patch AS jsonb), updated_at = now() "
    "WHERE owner_id = :owner_id AND id = :id RETURNING id"
)
_LOCK = text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))")
_PER_CLASS = text(
    "SELECT count(*) FROM class_outputs WHERE owner_id = :owner_id AND class_id = :class_id"
)
_PER_DAY = text(
    "SELECT count(*) FROM studio_output_starts "
    "WHERE owner_id = :owner_id AND created_at > now() - interval '24 hours'"
)
_START = text("INSERT INTO studio_output_starts (owner_id) VALUES (:owner_id)")
_TRIM = text(
    "DELETE FROM studio_output_starts "
    "WHERE owner_id = :owner_id AND created_at <= now() - make_interval(hours => :hours)"
)
_OPERATIONS = text(
    "SELECT o.idempotency_key, o.state, EXISTS (SELECT 1 FROM jobs j WHERE j.owner_id = "
    "o.owner_id AND j.id = o.job_id AND j.state = 'running' "
    "AND j.lease_version = o.lease_version) AS held "
    "FROM external_operations o WHERE o.owner_id = :owner_id AND o.class_id = :class_id "
    "AND o.idempotency_key IN :keys"
).bindparams(bindparam("keys", expanding=True))


@dataclass(frozen=True, slots=True)
class OutputRow:
    id: UUID
    class_id: UUID
    revision_id: UUID
    output: str
    attempt: int
    state: str
    external_id: str | None
    detail: dict[str, Any]


def _row(found: Any) -> OutputRow:
    return OutputRow(
        found.id,
        found.class_id,
        found.revision_id,
        found.output,
        found.attempt,
        found.state,
        found.external_id,
        dict(found.detail or {}),
    )


async def latest(
    db: AsyncSession,
    owner_id: UUID,
    class_id: UUID,
    revision_id: UUID,
    output: str,
    *,
    lock: bool = False,
) -> OutputRow | None:
    params = {
        "owner_id": owner_id,
        "class_id": class_id,
        "revision_id": revision_id,
        "output": output,
    }
    found = (await db.execute(_LATEST_LOCKED if lock else _LATEST_READ, params)).first()
    return None if found is None else _row(found)


async def all_latest(db: AsyncSession, owner_id: UUID, class_id: UUID) -> list[OutputRow]:
    """The newest attempt of every output of every draft of the class."""
    params = {"owner_id": owner_id, "class_id": class_id}
    return [_row(r) for r in (await db.execute(_ALL_LATEST, params)).all()]


async def check_limits(db: AsyncSession, owner_id: UUID, class_id: UUID) -> None:
    """``output_limit`` unless one more attempt fits the class and the user's day."""
    await db.execute(_LOCK, {"key": f"studio_output_starts:{owner_id}"})
    per_class = await db.execute(_PER_CLASS, {"owner_id": owner_id, "class_id": class_id})
    per_day = await db.execute(_PER_DAY, {"owner_id": owner_id})
    if per_class.scalar_one() >= ATTEMPTS_PER_CLASS or per_day.scalar_one() >= STARTS_PER_DAY:
        raise JobFailure(LIMIT_CATEGORY)


async def open_attempt(
    db: AsyncSession,
    owner_id: UUID,
    class_id: UUID,
    revision_id: UUID,
    output: str,
    attempt: int,
    detail: Mapping[str, Any],
) -> OutputRow:
    """Attempt ``attempt`` with ``detail`` stored, or the row a racing insert made."""
    await check_limits(db, owner_id, class_id)
    params = {
        "owner_id": owner_id,
        "class_id": class_id,
        "revision_id": revision_id,
        "output": output,
        "attempt": attempt,
        "detail": json.dumps(dict(detail), separators=(",", ":"), allow_nan=False),
    }
    if (await db.execute(_INSERT, params)).first() is not None:
        await db.execute(_START, {"owner_id": owner_id})
        await db.execute(_TRIM, {"owner_id": owner_id, "hours": KEEP_HOURS})
    found = await latest(db, owner_id, class_id, revision_id, output, lock=True)
    if found is None or found.attempt != attempt:
        raise JobFailure("internal")
    return found


async def update(
    db: AsyncSession,
    owner_id: UUID,
    row_id: UUID,
    *,
    state: str | None = None,
    external_id: str | None = None,
    patch: Mapping[str, Any] | None = None,
) -> None:
    params = {
        "owner_id": owner_id,
        "id": row_id,
        "state": state,
        "external_id": external_id,
        "patch": json.dumps(dict(patch or {}), separators=(",", ":"), allow_nan=False),
    }
    if (await db.execute(_UPDATE, params)).first() is None:
        raise JobFailure("internal")


def step_keys(row: OutputRow) -> dict[str, str]:
    """Each step of ``row``'s output to its operation key."""
    return {
        step: key(row.class_id, row.revision_id, row.output, row.attempt, step)
        for step, output in STEPS.items()
        if output == row.output
    }


async def operations(
    db: AsyncSession, owner_id: UUID, row: OutputRow
) -> dict[str, tuple[str, bool]]:
    """Each step of ``row`` that has an operation: ``(state, held by a running job)``."""
    keys = step_keys(row)
    by_key = {v: k for k, v in keys.items()}
    params = {"owner_id": owner_id, "class_id": row.class_id, "keys": list(keys.values())}
    found = (await db.execute(_OPERATIONS, params)).all()
    return {by_key[r.idempotency_key]: (r.state, bool(r.held)) for r in found}
