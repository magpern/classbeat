"""``mowl_update_workout`` (ADR 0007 sections 7 and 8): an existing program of the user's MOWL
account re-saved in place, as an external operation.

- **Key** ``<job_id>:update`` (a job's own step, ADR 0005 section 7) and **request hash** over
  ``{kind, program_id, content_hash (hex), playlist_id}``: what the write does, never who asks.
  A retry that would write another program, revision or playlist is a hash mismatch
  (``internal``), never a write.
- **Context** (stored with every attempt's ``pending`` row): ``{saved_workout_id, revision_id,
  content_hash, playlist_id, program_id, before_hash, baseline}`` plus the protocol's
  ``bound_seconds``. ``before_hash`` is the hex SHA-256 of the program's normalized state read
  just before the write (``classbeat.mowl_state``; null when the read program could not be
  normalized, which only an ``overwrite`` lets through), ``baseline`` the ProgramIDs of My
  Workouts' first page read in ``prepare``.
- **On success** (``on_succeeded``, same transaction as the ``succeeded`` row, whoever marks it):
  the ``saved_workouts`` row named by the context is moved to the written revision, **only** when
  it is live, has the operation's program id and belongs to the operation's own class (one
  statement joining the saved row, the operation and the revision on ``owner_id`` and
  ``class_id``). Its ``state_hash`` is cleared: only a read-back (``verify``) sets it again.
  ``operation_id`` keeps naming the creating operation. Re-applying it is harmless.
- **The reconciler** (bounded: one program read, at most one listing) reads the target first:

  ===============================================================  ============================
  the target ``matches`` the intended spec                          ``succeeded`` (its id)
  the target equals ``before_hash`` **and** the listing is covered  ``failed``: provably
  **and** holds no program outside the baseline                     unchanged, nothing created
  the target equals ``before_hash`` but a new program appeared, or  inconclusive (MOWL may have
  the listing is not covered or fails                               made a copy)
  anything else (a partial update), the target gone or unreadable   inconclusive
  ===============================================================  ============================

  The listing is **covered** when it has fewer than ``PAGE_SIZE`` programs or holds a program of
  the baseline. A ``failed`` row is retried by the job (``NO_EFFECT_CATEGORY``), whose next
  attempt starts at ``prepare`` again: a fresh baseline and a fresh read-first comparison.
"""

import logging
from collections.abc import Mapping, Sequence
from typing import Any, Final, cast
from uuid import UUID

from mowl import api as mowl_api
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from classbeat import mowl_state
from classbeat.connections import ConnectionFailure, call_opened, open_connection
from classbeat.operations.kinds import (
    INCONCLUSIVE,
    OPERATION_KINDS,
    Operation,
    OperationKind,
    ReconcileInput,
    Reconciliation,
    request_hash,
)
from classbeat.saving import reads
from classbeat.saving.operation import PAGE_SIZE, listed_ids

logger = logging.getLogger(__name__)

KIND: Final = "mowl_update_workout"
KEY_SUFFIX: Final = ":update"
MAX_ID: Final = 2**63 - 1


def idempotency_key(job_id: UUID) -> str:
    return f"{job_id}{KEY_SUFFIX}"


def update_hash(program_id: int, content_hash_hex: str, playlist_id: int) -> bytes:
    return request_hash(
        KIND,
        program_id=str(program_id),
        content_hash=content_hash_hex,
        playlist_id=str(playlist_id),
    )


def state_hex(program: object) -> str | None:
    """The hex SHA-256 of a read program's normalized state, None when it can't be normalized."""
    try:
        return mowl_state.state_hash(mowl_state.normalized_program(program)).hex()
    except ValueError:
        return None


def update_context(
    saved_workout_id: UUID,
    revision_id: UUID,
    content_hash_hex: str,
    playlist_id: int,
    program_id: int,
    before_hash: str | None,
    baseline: Sequence[int],
) -> dict[str, Any]:
    return {
        "saved_workout_id": str(saved_workout_id),
        "revision_id": str(revision_id),
        "content_hash": content_hash_hex,
        "playlist_id": playlist_id,
        "program_id": program_id,
        "before_hash": before_hash,
        "baseline": list(baseline),
    }


def _whole(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or not 0 < value <= MAX_ID:
        return None
    return value


def _known(context: Mapping[str, Any]) -> tuple[int, int, set[int], str | None] | None:
    """``(program_id, playlist_id, baseline, before_hash)`` from a stored context, or None."""
    program_id = _whole(context.get("program_id"))
    playlist_id = _whole(context.get("playlist_id"))
    baseline = context.get("baseline")
    before = context.get("before_hash")
    if program_id is None or playlist_id is None or not isinstance(baseline, list):
        return None
    if before is not None and not isinstance(before, str):
        return None
    ids: set[int] = set()
    for value in cast(list[Any], baseline):
        whole = _whole(value)
        if whole is None:
            return None
        ids.add(whole)
    return program_id, playlist_id, ids, before


async def _reconcile(check: ReconcileInput) -> Reconciliation:
    operation = check.operation
    if operation.class_id is None or check.keyring is None:
        return INCONCLUSIVE
    known = _known(operation.context)
    if known is None:
        return INCONCLUSIVE
    program_id, playlist_id, baseline, before = known
    spec = await reads.saved_spec(check.factory, check.ctx, operation.class_id, operation.context)
    if spec is None:
        return INCONCLUSIVE
    try:
        opened = await open_connection(check.factory, check.keyring, check.ctx, "mowl")
        program = await call_opened(
            check.factory, check.ctx, opened, lambda c: mowl_api.get_workout(c, program_id)
        )
    except ConnectionFailure:
        return INCONCLUSIVE  # gone, or not readable now: nothing is proved
    if mowl_state.program_id_of(program) != program_id:
        return INCONCLUSIVE
    if mowl_state.matches(spec, program, playlist_id=playlist_id):
        return Reconciliation("succeeded", str(program_id))
    if before is None or state_hex(program) != before:
        return INCONCLUSIVE  # partly updated, or changed since: the effect may have happened
    # The target is unchanged. That alone proves nothing: MOWL may have made a copy (section 7).
    try:
        listing = await call_opened(
            check.factory, check.ctx, opened, lambda c: mowl_api.list_workouts(c, page=0)
        )
    except ConnectionFailure:
        return INCONCLUSIVE
    ids = listed_ids(listing)
    if ids is None:
        return INCONCLUSIVE
    covered = len(ids) < PAGE_SIZE or any(i in baseline for i in ids)
    if not covered or any(i not in baseline for i in ids):
        return INCONCLUSIVE
    return Reconciliation("failed")


# The saved row of the operation's own class: live, the operation's program, moved to the
# revision the operation wrote (a draft of the same class with the stored hash).
_UPDATED = text(
    "UPDATE saved_workouts AS s SET revision_id = r.id, revision_number = r.number, "
    "content_hash = r.content_hash, name = left(r.content ->> 'name', 100), "
    "playlist_id = :playlist_id, state_hash = NULL, updated_at = now() "
    "FROM external_operations AS o, class_revisions AS r "
    "WHERE s.owner_id = :owner_id AND s.id = :saved_workout_id AND s.detached_at IS NULL "
    "AND s.program_id = :program_id AND s.class_id = o.class_id "
    "AND o.owner_id = s.owner_id AND o.id = :operation_id AND o.kind = :kind "
    "AND o.state = 'succeeded' AND o.external_id = :external_id "
    "AND r.owner_id = o.owner_id AND r.class_id = o.class_id AND r.id = :revision_id "
    "AND r.kind = 'draft' AND r.content_hash = :content_hash "
    "RETURNING s.id"
)


async def _write_updated(db: AsyncSession, owner_id: UUID, operation: Operation) -> None:
    context = operation.context
    try:
        saved_workout_id = UUID(str(context["saved_workout_id"]))
        revision_id = UUID(str(context["revision_id"]))
        content_hash = bytes.fromhex(str(context["content_hash"]))
    except (KeyError, ValueError):
        logger.warning("saved workout not updated: unreadable operation", extra={"kind": KIND})
        return
    program_id = _whole(context.get("program_id"))
    playlist_id = _whole(context.get("playlist_id"))
    if (
        operation.kind != KIND
        or operation.class_id is None
        or program_id is None
        or playlist_id is None
        or operation.external_id != str(program_id)
    ):
        logger.warning("saved workout not updated: not this update", extra={"kind": KIND})
        return
    await db.execute(
        _UPDATED,
        {
            "owner_id": owner_id,
            "saved_workout_id": saved_workout_id,
            "program_id": program_id,
            "playlist_id": playlist_id,
            "operation_id": operation.id,
            "kind": KIND,
            "external_id": operation.external_id,
            "revision_id": revision_id,
            "content_hash": content_hash,
        },
    )


UPDATE: Final = OPERATION_KINDS.register(OperationKind(KIND, _reconcile, _write_updated))
