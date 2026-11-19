"""``spotify_create_playlist`` (ADR 0008 section 2): a new public, non-collaborative playlist in
the user's own Spotify account (roadmap Amendment 30), as an external operation. Create only: the
tracks are written by ``spotify_replace_items`` afterwards, so the id exists on a row between the
two requests.

- **Key** ``<job_id>:pool-create`` or ``<job_id>:final-create`` (a studio output's is its
  approval's, ``out:...:spotify_create``, ADR 0036 section 6): one row per job, never one per
  user, so a later job (after ``pool_missing``, a revoked and given-again consent, or a superseded
  uncertain create) makes a new row with a new id and therefore a new ``ref``. Only the attempts
  of one job share a row, and they take a ``failed`` row over only when the create provably did
  not happen (refused before it was sent, or refused by Spotify; #198 item 1):

  - the job's own reconciliation never resolves a create ``failed`` (``_JOB_KINDS``): "none
    listed after the lag" is not proof, so there it is inconclusive and the job fails
    ``uncertain_write``; the lag rule below is the user's (Check again), whose next create is a
    new job under a new key;
  - the decide step (``_retake_guard``) refuses a ``failed`` row that the call's own record did
    not write (``resolved_by`` other than ``worker``: the user's Check again or Do it anyway),
    with ``uncertain_write`` and nothing sent.
- **Request hash** over ``{kind, purpose, name, pool_id}`` (the pool) or ``{kind, purpose, name,
  content_hash}`` (the final playlist, the approved ``song_list`` revision's hash).
- **Context** ``{purpose, pool_id}`` or ``{purpose, revision_id, content_hash, name,
  spotify_user_id}``, plus the protocol's ``ref`` (12 hex characters of the row's id). The
  playlist's description carries ``classbeat ref <ref>``.
- **The call** is one request (``single_request``): ``POST /me/playlists`` with no tracks and
  ``public: true``, ``collaborative: false`` (``create_call``; Amendment 30, #1287). Nothing asks
  for private afterwards and nothing reads it back for its privacy.
  A 403 to it is ``failed`` ``forbidden`` like any refusal (a connection made before Amendment 27
  may lack ``playlist-modify-public``; the 403 does not say so, so it is not guessed).
  A request refused before it left, or answered with a refusal (4xx), is ``failed``; anything
  else (dropped after sending, a 5xx, an answer without a valid id) is ``uncertain``.
- **On success** (``create_record``): the pool's ``spotify_pool.playlist_id``, or the final
  playlist's ``class_playlists`` row of the operation's own class and revision.
- **The reconciler** lists the user's playlists (at most 20 pages of 50) for exactly one owned
  by the connected user (public either way, Amendment 27), whose description names this
  row's ``ref``:

  ======================================================  ===================================
  exactly one                                             ``succeeded`` with its id
  none, every page read (fewer than 1000), the call      ``failed`` (the user's Check again;
  returned and ``CLASSBEAT_SPOTIFY_LISTING_LAG_SECONDS``  inconclusive in the job's own
  set and passed since                                    reconciliation, ``_JOB_KINDS``)
  none otherwise (the lag unset by default)               inconclusive
  two or more, a failed listing, 20 full pages            inconclusive
  ======================================================  ===================================

  A same-named playlist without this ``ref`` is never taken for ours.
"""

import logging
from datetime import UTC, datetime, timedelta
from typing import Any, Final, cast
from uuid import UUID

from mowl import spotify_web
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from classbeat.connections import ConnectionFailure, OpenConnection, open_connection
from classbeat.crypto.keks import KeyRing
from classbeat.egress import OutboundBlocked
from classbeat.jobs import JobFailure, JobRun
from classbeat.operations import (
    INCONCLUSIVE,
    OPERATION_KINDS,
    CallResult,
    Operation,
    OperationKind,
    OperationKinds,
    OperationRequest,
    ReconcileInput,
    Reconciliation,
    UncertainAnswer,
    perform_for,
    request_hash,
)
from classbeat.operations.kinds import REF_KEY, SPOTIFY_CREATE_KIND, ref_of
from classbeat.operations.requests import Guard
from classbeat.playlists import common, create_call
from classbeat.playlists.create_record import on_succeeded, purpose_fits

logger = logging.getLogger(__name__)

KIND: Final = SPOTIFY_CREATE_KIND
# One request (30 s), plus a refresh before it and one after a 401 (10 s each), with room.
TIMEOUT_SECONDS: Final = 60.0


def pool_key(job_id: UUID) -> str:
    return f"{job_id}:pool-create"


def final_key(job_id: UUID) -> str:
    return f"{job_id}:final-create"


def pool_request(owner_id: UUID, job_id: UUID, pool_id: UUID, guard: Guard) -> OperationRequest:
    key = pool_key(job_id)
    return OperationRequest(
        kind=KIND,
        idempotency_key=key,
        request_hash=request_hash(
            KIND, purpose="pool", name=common.POOL_NAME, pool_id=str(pool_id)
        ),
        timeout_seconds=TIMEOUT_SECONDS,
        guard=_retake_guard(owner_id, key, guard),
        single_request=True,
        context={"purpose": "pool", "pool_id": str(pool_id)},
        with_ref=True,
    )


def final_request(
    owner_id: UUID,
    job_id: UUID,
    class_id: UUID,
    revision_id: UUID,
    content_hash: bytes,
    name: str,
    account: str,
    guard: Guard,
    key: str | None = None,  # a studio output's, bound to its approval (ADR 0036 section 6)
) -> OperationRequest:
    if len(content_hash) != 32:
        raise JobFailure("invalid_input")
    name = common.check_name(name)
    key = key or final_key(job_id)
    return OperationRequest(
        kind=KIND,
        idempotency_key=key,
        request_hash=request_hash(
            KIND, purpose="final", name=name, content_hash=content_hash.hex()
        ),
        timeout_seconds=TIMEOUT_SECONDS,
        guard=_retake_guard(owner_id, key, guard),
        single_request=True,
        class_id=class_id,
        context={
            "purpose": "final",
            "revision_id": str(revision_id),
            "content_hash": content_hash.hex(),
            "name": name,
            "spotify_user_id": account,
        },
        with_ref=True,
    )


# This job's own create row, locked whatever its state (so a Do it anyway cannot turn it
# ``failed`` between this check and the protocol's take-over in the same transaction).
_OWN_ROW = text(
    "SELECT state, resolved_by FROM external_operations WHERE owner_id = :owner_id "
    "AND kind = :kind AND idempotency_key = :key FOR UPDATE"
)


def _retake_guard(owner_id: UUID, key: str, inner: Guard) -> Guard:
    """``inner`` (the step's own guard), then: a ``failed`` row is taken over only when the
    call's own record wrote it (``resolved_by = 'worker'``, a refusal that proves no effect). The
    job's reconciliation never writes ``failed`` for a create (``_JOB_KINDS``), so a ``worker``
    ``failed`` row of this key is such a record, with one exception: the pool supersede rule
    (``classbeat.playlists.pool``) also marks rows ``failed`` with ``resolved_by = 'worker'``
    although they may have been created. Those are safe only because they belong to other jobs
    (the supersede skips the job's own key) that are neither queued nor running when they are
    marked (``operation_busy`` otherwise), so no attempt of theirs runs the pool create again,
    and keys are per job. Anything else may have been created.
    Built into every create request by ``pool_request`` and ``final_request``."""

    async def guard(db: AsyncSession) -> bool:
        if not await inner(db):
            return False
        row = (await db.execute(_OWN_ROW, {"owner_id": owner_id, "kind": KIND, "key": key})).first()
        if row is not None and row.state == "failed" and row.resolved_by != "worker":
            logger.warning("create not sent again: not proven absent", extra={"kind": KIND})
            raise JobFailure("uncertain_write")
        return True

    return guard


async def create(
    run: JobRun, keyring: KeyRing, opened: OpenConnection, request: OperationRequest, name: str
) -> str:
    """Perform the create of ``request`` with the held connection ``opened``; the new id."""
    purpose = str(request.context["purpose"])

    async def call(operation_id: UUID) -> CallResult:
        about = common.description(ref_of(operation_id), purpose)
        answer = await common.call(
            run.factory,
            keyring,
            run.ctx,
            opened,
            create_call.create(name, about),
            write=True,
            single_request=True,  # one POST: a refusal proves it had no effect
        )
        if not isinstance(answer, str) or common.SPOTIFY_ID.fullmatch(answer) is None:
            raise UncertainAnswer
        return CallResult(answer)

    done = await perform_for(run, request, call, keyring=keyring, kinds=_JOB_KINDS)
    return common.check_playlist_id(done.external_id)


# --- the reconciler ----------------------------------------------------------------------------


def _lag_passed(operation: Operation) -> bool:
    lag = common.runtime().listing_lag_seconds
    if lag is None or not operation.call_returned or operation.call_ended_at is None:
        return False
    return datetime.now(UTC) >= operation.call_ended_at + timedelta(seconds=lag)


async def _reconcile(check: ReconcileInput) -> Reconciliation:
    operation = check.operation
    ref = operation.context.get(REF_KEY)
    if operation.kind != KIND or not purpose_fits(operation) or ref != ref_of(operation.id):
        return INCONCLUSIVE
    if check.keyring is None:
        return INCONCLUSIVE
    try:
        opened = await open_connection(check.factory, check.keyring, check.ctx, "spotify")
        if opened.account is None:
            return INCONCLUSIVE
        listing = await common.call(
            check.factory,
            check.keyring,
            check.ctx,
            opened,
            lambda c: spotify_web.my_playlists(c, common.MAX_LIST_PAGES),
        )
    except (ConnectionFailure, OutboundBlocked):
        return INCONCLUSIVE  # a failed listing proves nothing
    if not isinstance(listing, list):
        return INCONCLUSIVE
    entries = cast(list[Any], listing)
    if len(entries) >= common.MAX_LIST_PAGES * common.READ_PAGE:
        return INCONCLUSIVE  # 20 full pages: the playlist may be beyond them
    found: list[str] = []
    for entry in entries:
        if not isinstance(entry, dict):
            return INCONCLUSIVE
        playlist = cast(dict[str, Any], entry)
        playlist_id = playlist.get("id")
        if (
            isinstance(playlist_id, str)
            and common.SPOTIFY_ID.fullmatch(playlist_id) is not None
            and playlist.get("owner_id") == opened.account
            and isinstance(playlist.get("public"), bool)  # Amendment 27
            and ref in common.refs_in(playlist.get("description"))
        ):
            found.append(playlist_id)
    if len(found) == 1:
        return Reconciliation("succeeded", found[0])
    if not found and _lag_passed(operation):
        return Reconciliation("failed")
    return INCONCLUSIVE


CREATE: Final = OPERATION_KINDS.register(OperationKind(KIND, _reconcile, on_succeeded))


async def _reconcile_in_job(check: ReconcileInput) -> Reconciliation:
    """The job's own reconciliation: ``failed`` (none listed after the lag) is not proof that
    the create did not happen, and the job's next attempt would take the row over and send it
    again under the same ``ref`` (#198 item 1). It stays inconclusive for the user instead."""
    found = await _reconcile(check)
    return INCONCLUSIVE if found.outcome == "failed" else found


# What ``create`` hands ``perform_for``: the same kind, with the job's reconciler.
_JOB_KINDS: Final = OperationKinds()
_JOB_KINDS.register(OperationKind(KIND, _reconcile_in_job, on_succeeded))
