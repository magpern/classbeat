"""``mowl_save_workout`` (ADR 0007 sections 6 and 8): a new private program in the user's MOWL
account, as an external operation.

- **Key** ``<job_id>:save`` (a job's own step, ADR 0005 section 7) and **request hash** over
  ``{kind, content_hash (hex), playlist_id}``: what the write does, never who asks.
- **Context** (stored with every attempt's ``pending`` row): ``{revision_id, content_hash,
  playlist_id, baseline}``, plus the protocol's ``bound_seconds``. ``baseline`` is the
  ProgramIDs of My Workouts' first page read just before the write.
- **The bound**: ``30 s * (3 + 3 * segments)``, at most 30 minutes; the call runs inside
  ``classbeat.egress.call_deadline(bound)``, which ends every request of it by then (each request
  also has its own total deadline of ``REQUEST_SECONDS``).
- **On success** (``on_succeeded``, same transaction as the ``succeeded`` row, whoever marks it:
  the worker, the reconciliation or the user's Check again): the ``saved_workouts`` row. It is
  built from the operation row and the revision **of the operation's own class** (one statement
  joining both on ``owner_id`` and ``class_id``), so it can only ever name the class, revision
  and operation of one owner and one class. ``ON CONFLICT DO NOTHING`` makes it idempotent.
- **The reconciler** (bounded: one listing, at most ``MAX_PROGRAM_READS`` program reads) compares
  My Workouts' first page now with the baseline. Only *new* programs count:

  ========================================================  =================================
  exactly one new program that is private and ``matches``   ``succeeded`` with its ProgramID
  two or more new matching programs                         inconclusive
  new programs, none matching (a partial save)               inconclusive
  no new program, listing covered                            inconclusive, or ``failed`` when
                                                             ``CLASSBEAT_MOWL_LISTING_LAG_SECONDS``
                                                             is set, the call returned and
                                                             ``now >= call_ended_at + lag``
  listing not covered (50 new programs, or it failed)        inconclusive
  ========================================================  =================================

  The listing is **covered** when it has fewer than ``PAGE_SIZE`` programs or holds a program of
  the baseline (newest first). A program read that fails, more candidates than reads, or anything
  the reconciler can't parse is inconclusive. The spec it compares with is rebuilt from the
  stored revision (``classbeat.saving.reads``).

The lag is unset by default (``configure_listing_lag``): absence is not proof until the owner has
measured MOWL's listing delay at live activation.
"""

import logging
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta
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

logger = logging.getLogger(__name__)

KIND: Final = "mowl_save_workout"
KEY_SUFFIX: Final = ":save"
PAGE_SIZE: Final = 50  # ``api.list_workouts``' page
REQUEST_SECONDS: Final = 30.0  # the total deadline of one request (the library's timeout)
MAX_BOUND_SECONDS: Final = 1800.0
MAX_PROGRAM_READS: Final = 3

_lag: float | None = None


def configure_listing_lag(seconds: float | None) -> None:
    """``CLASSBEAT_MOWL_LISTING_LAG_SECONDS`` (None: absence never proves anything)."""
    global _lag
    if seconds is not None and not seconds > 0:
        raise ValueError("the listing lag is positive")
    _lag = seconds


def bound_seconds(segments: int) -> float:
    """The call's total bound: one request deadline per request the library makes (a program,
    three per segment, the segment list), one to spare, at most ``MAX_BOUND_SECONDS``."""
    if segments < 1:
        raise ValueError("a workout has segments")
    return min(REQUEST_SECONDS * (3 + 3 * segments), MAX_BOUND_SECONDS)


def idempotency_key(job_id: UUID) -> str:
    return f"{job_id}{KEY_SUFFIX}"


def save_hash(content_hash_hex: str, playlist_id: int) -> bytes:
    return request_hash(KIND, content_hash=content_hash_hex, playlist_id=str(playlist_id))


def save_context(
    revision_id: UUID, content_hash_hex: str, playlist_id: int, baseline: Sequence[int]
) -> dict[str, Any]:
    return {
        "revision_id": str(revision_id),
        "content_hash": content_hash_hex,
        "playlist_id": playlist_id,
        "baseline": list(baseline),
    }


def listed_ids(listing: object) -> list[int] | None:
    """The ProgramIDs of a listing page, in order; None when it is not a list of programs with
    ids (a baseline or a comparison must never miss one)."""
    if not isinstance(listing, list):
        return None
    ids: list[int] = []
    for item in cast(list[Any], listing):
        program_id = mowl_state.program_id_of(item)
        if program_id is None:
            return None
        ids.append(program_id)
    return ids


def _context_ints(context: Mapping[str, Any]) -> tuple[set[int], int] | None:
    baseline = context.get("baseline")
    playlist_id = context.get("playlist_id")
    if not isinstance(baseline, list) or isinstance(playlist_id, bool):
        return None
    if not isinstance(playlist_id, int):
        return None
    ids: set[int] = set()
    for value in cast(list[Any], baseline):
        if isinstance(value, bool) or not isinstance(value, int):
            return None
        ids.add(value)
    return ids, playlist_id


def _names(listing: list[Any]) -> dict[int, object]:
    out: dict[int, object] = {}
    for item in listing:
        program_id = mowl_state.program_id_of(item)
        if program_id is not None and isinstance(item, Mapping):
            out[program_id] = cast(Mapping[str, Any], item).get("Name")
    return out


def _lag_passed(operation: Operation) -> bool:
    lag = _lag
    if lag is None or not operation.call_returned or operation.call_ended_at is None:
        return False
    return datetime.now(UTC) >= operation.call_ended_at + timedelta(seconds=lag)


async def _reconcile(check: ReconcileInput) -> Reconciliation:
    operation = check.operation
    if operation.class_id is None or check.keyring is None:
        return INCONCLUSIVE
    known = _context_ints(operation.context)
    if known is None:
        return INCONCLUSIVE
    baseline, playlist_id = known
    spec = await reads.saved_spec(check.factory, check.ctx, operation.class_id, operation.context)
    if spec is None:
        return INCONCLUSIVE
    try:
        opened = await open_connection(check.factory, check.keyring, check.ctx, "mowl")
        listing = await call_opened(
            check.factory, check.ctx, opened, lambda c: mowl_api.list_workouts(c, page=0)
        )
    except ConnectionFailure:
        return INCONCLUSIVE
    ids = listed_ids(listing)
    if ids is None:
        return INCONCLUSIVE
    covered = len(ids) < PAGE_SIZE or any(i in baseline for i in ids)
    if not covered:
        return INCONCLUSIVE
    new = [i for i in ids if i not in baseline]
    if not new:
        return Reconciliation("failed") if _lag_passed(operation) else INCONCLUSIVE
    # Only a new program of the spec's name can be the effect; a listing without names leaves
    # every new program a candidate.
    names = _names(cast(list[Any], listing))
    candidates = [i for i in new if names.get(i) in (None, spec["name"])]
    if not candidates or len(candidates) > MAX_PROGRAM_READS:
        return INCONCLUSIVE  # a partial save, or more than can be read
    found: list[int] = []
    for program_id in candidates:
        try:
            program = await call_opened(
                check.factory,
                check.ctx,
                opened,
                lambda c, p=program_id: mowl_api.get_workout(c, p),
            )
        except ConnectionFailure:
            return INCONCLUSIVE  # it may have been a second match
        if mowl_state.matches(spec, program, playlist_id=playlist_id):
            found.append(program_id)
    if len(found) != 1:
        return INCONCLUSIVE
    return Reconciliation("succeeded", str(found[0]))


# The saved row, from the operation and the revision of the operation's own class.
_SAVED = text(
    "INSERT INTO saved_workouts (id, owner_id, class_id, program_id, playlist_id, revision_id, "
    "revision_number, content_hash, name, operation_id) "
    "SELECT gen_random_uuid(), o.owner_id, o.class_id, :program_id, :playlist_id, r.id, r.number, "
    "r.content_hash, left(r.content ->> 'name', 100), o.id "
    "FROM external_operations o JOIN class_revisions r ON r.owner_id = o.owner_id "
    "AND r.class_id = o.class_id AND r.id = :revision_id "
    "WHERE o.owner_id = :owner_id AND o.id = :operation_id AND o.kind = :kind "
    "AND o.state = 'succeeded' AND o.external_id = :external_id AND o.class_id = :class_id "
    "AND r.kind = 'draft' AND r.content_hash = :content_hash "
    "ON CONFLICT DO NOTHING RETURNING id"
)


async def _write_saved(db: AsyncSession, owner_id: UUID, operation: Operation) -> None:
    external_id = operation.external_id
    context = operation.context
    try:
        program_id = int(external_id or "")
        revision_id = UUID(str(context["revision_id"]))
        content_hash = bytes.fromhex(str(context["content_hash"]))
        playlist_id = context["playlist_id"]
    except (KeyError, ValueError):
        logger.warning("saved workout not written: unreadable operation", extra={"kind": KIND})
        return
    if operation.kind != KIND or operation.class_id is None or not 0 < program_id <= 2**63 - 1:
        logger.warning("saved workout not written: not a save", extra={"kind": KIND})
        return
    if isinstance(playlist_id, bool) or not isinstance(playlist_id, int):
        logger.warning("saved workout not written: no playlist", extra={"kind": KIND})
        return
    await db.execute(
        _SAVED,
        {
            "owner_id": owner_id,
            "operation_id": operation.id,
            "kind": KIND,
            "external_id": external_id,
            "class_id": operation.class_id,
            "revision_id": revision_id,
            "content_hash": content_hash,
            "program_id": program_id,
            "playlist_id": playlist_id,
        },
    )


SAVE: Final = OPERATION_KINDS.register(OperationKind(KIND, _reconcile, _write_saved))
