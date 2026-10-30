"""``spotify_replace_items`` (ADR 0008 section 2): set a playlist classbeat created to exactly this
ordered list of tracks, then read it back. Absolute, so repeating it is safe (unlike a create).

- **Key** ``<job_id>:pool:<h16>`` (``h16``: the first 16 hex characters of the list's hash; a
  retried stage may propose other candidates) or ``<job_id>:final-fill``. **Request hash** over
  ``{kind, playlist_id, uris_hash}``. **Context** ``{purpose, playlist_id, uris_hash, count}``
  (and ``pool_id`` for the pool) plus the protocol's ``bound_seconds``.
- **Several requests** (``kinds.MULTI_REQUEST_KINDS``): ``bound_seconds`` = 30 s x (3 + 2 x
  writes + reads), at most 30 minutes; the call runs inside ``call_deadline_until`` of an instant
  taken before the ``pending`` row is written plus the bound, so no request of it is sent after
  ``pending_at + bound``, and ``call_may_be_running`` and ``actionable_at`` apply to its row.
- **The call** (one worker task, each step through ``spotify_call`` with the held connection):
  the **owner check** ``GET /playlists/{id}`` (the connected user owns it, ``public`` and
  ``collaborative`` exactly ``false``), then ``PUT /items`` with the first 100 URIs (an empty
  list clears) and ``POST /items`` for the rest, then **the read-back** of every item and an
  exact comparison of the ordered track ids.

  =========================================================  ================================
  owner check answered 404                                    ``failed`` ``pool_missing``
                                                              (final: ``not_found``)
  owner check answered 403                                    ``failed`` ``forbidden``
  owner check answered another 4xx, or not sent at all       ``failed`` with its category
  (the connection is no longer the one the decide step held)
  readable, but not owned, public or collaborative            ``failed`` ``pool_unusable``
                                                              (final: ``forbidden``)
  the read-back equals the list                               ``succeeded`` (``external_id`` =
                                                              the playlist id, ``result``
                                                              ``{snapshot_id, count}``)
  the read-back is a different list                           ``failed`` ``playlist_mismatch``
  anything else (refused, dropped, a 5xx, a partial write,   ``uncertain``
  an unreadable read-back), before or after a write
  =========================================================  ================================

  In each ``failed`` row before the writes nothing was written: no write request is sent unless
  the owner check passed. classbeat never changes a playlist's privacy itself.
- **The reconciler** reads the playlist the same way (owner check, then items): the list equals
  the one written, ``succeeded``; readable and different, ``failed`` once the call is over (it
  returned, or ``actionable_at`` passed), inconclusive before; unreadable or no longer owned and
  private, inconclusive.
- **On success** (same transaction): pool, ``spotify_pool.last_hash`` and ``last_written_at``
  while the pool row still holds this playlist; final, the class's live ``class_playlists`` row of
  this playlist becomes ``verified`` with ``content_hash`` and ``track_count``.
"""

import logging
import math
import time
from datetime import UTC, datetime, timedelta
from typing import Final
from uuid import UUID

from mowl import spotify_web
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from classbeat.connections import ConnectionFailure, OpenConnection, open_connection
from classbeat.connections.errors import NEVER_SENT
from classbeat.crypto.keks import KeyRing
from classbeat.egress import OutboundBlocked
from classbeat.jobs import JobFailure, JobRun, failure_of
from classbeat.operations import (
    INCONCLUSIVE,
    OPERATION_KINDS,
    CallResult,
    NotWritten,
    Operation,
    OperationKind,
    OperationRequest,
    ReconcileInput,
    Reconciliation,
    UncertainAnswer,
    perform,
    request_hash,
)
from classbeat.operations.kinds import (
    CALL_MAY_RUN_MARGIN_SECONDS,
    FALLBACK_BOUND_SECONDS,
    MAX_BOUND_SECONDS,
    SPOTIFY_REPLACE_KIND,
    spotify_purpose,
)
from classbeat.operations.requests import Guard
from classbeat.playlists import common

logger = logging.getLogger(__name__)

KIND: Final = SPOTIFY_REPLACE_KIND
HASH_PREFIX: Final = 16
# The categories of a failed owner check, by purpose (ADR 0008 section 2): the pool's own words
# for "gone" and "changed"; a final playlist is one classbeat just created for the class.
MISSING: Final = {"pool": "pool_missing", "final": "not_found"}
UNUSABLE: Final = {"pool": "pool_unusable", "final": "forbidden"}


def bound_seconds(count: int) -> float:
    """30 s per request: the owner check and two spare, two per write (the ``PUT`` and each
    ``POST`` of 100), one per read-back page of 50. At most ``MAX_BOUND_SECONDS``."""
    writes = max(1, math.ceil(count / common.WRITE_CHUNK))
    reads = max(1, math.ceil(count / common.READ_PAGE))
    return min(common.REQUEST_SECONDS * (3 + 2 * writes + reads), float(MAX_BOUND_SECONDS))


def pool_key(job_id: UUID, digest: bytes) -> str:
    return f"{job_id}:pool:{digest.hex()[:HASH_PREFIX]}"


def final_key(job_id: UUID) -> str:
    return f"{job_id}:final-fill"


def replace_request(
    key: str,
    purpose: str,
    playlist_id: str,
    track_ids: tuple[str, ...],
    guard: Guard,
    *,
    class_id: UUID | None = None,
    pool_id: UUID | None = None,
) -> OperationRequest:
    digest = common.ids_hash(track_ids)
    context: dict[str, object] = {
        "purpose": purpose,
        "playlist_id": common.check_playlist_id(playlist_id),
        "uris_hash": digest.hex(),
        "count": len(track_ids),
    }
    if pool_id is not None:
        context["pool_id"] = str(pool_id)
    return OperationRequest(
        kind=KIND,
        idempotency_key=key,
        request_hash=request_hash(KIND, playlist_id=playlist_id, uris_hash=digest.hex()),
        timeout_seconds=common.REQUEST_SECONDS,
        guard=guard,
        single_request=False,  # several requests: only a refused owner check proves no effect
        class_id=class_id,
        context=context,
        bound_seconds=bound_seconds(len(track_ids)),
    )


def _owner_check_failure(failure: ConnectionFailure, purpose: str) -> str | None:
    """The category of an owner check that proves nothing was written: Spotify answered it with a
    refusal (4xx), or the connection was not the held one and nothing was sent at all
    (``NEVER_SENT``). None for anything else (refused before sending, dropped, a 5xx), which is
    uncertain."""
    if failure.category in NEVER_SENT:
        return failure.category
    status = failure.http_status
    if status is None or not 400 <= status <= 499:
        return None
    if status == 404:
        return MISSING[purpose]
    if status == 403:
        return "forbidden"
    return failure_of(failure).category


async def replace(
    run: JobRun,
    keyring: KeyRing,
    opened: OpenConnection,
    request: OperationRequest,
    track_ids: tuple[str, ...],
) -> CallResult:
    """Perform the replace of ``request`` with the held connection ``opened``."""
    purpose = str(request.context["purpose"])
    playlist_id = common.check_playlist_id(request.context["playlist_id"])
    uris = common.track_uris(track_ids)
    bound = request.bound_seconds
    assert bound is not None  # noqa: S101 - replace_request sets it
    # Anchored before ``perform`` writes ``pending`` (``pending_at = now()``): every request of the
    # call ends by ``pending_at + bound``, however late it starts (ADR 0007 section 6).
    deadline_at = time.monotonic() + bound
    written: list[CallResult] = []

    async def call() -> CallResult:
        try:
            found = await common.call(
                run.factory,
                keyring,
                run.ctx,
                opened,
                lambda c: spotify_web.get_playlist(c, playlist_id),
                deadline_at=deadline_at,
            )
        except ConnectionFailure as failure:
            refused = _owner_check_failure(failure, purpose)
            if refused is not None:
                raise NotWritten(refused) from None
            raise UncertainAnswer from None
        if not common.owned(found, playlist_id, opened.account):
            raise NotWritten(UNUSABLE[purpose])
        try:
            snapshot = await common.call(
                run.factory,
                keyring,
                run.ctx,
                opened,
                lambda c: spotify_web.replace_playlist_items(c, playlist_id, uris),
                write=True,
                deadline_at=deadline_at,
            )
            entries = await common.call(
                run.factory,
                keyring,
                run.ctx,
                opened,
                lambda c: spotify_web.playlist_items(c, playlist_id),
                deadline_at=deadline_at,
            )
        except ConnectionFailure:
            # After a write request may have been sent: never ``failed`` (not even a category
            # that says the connection could not be opened, since an earlier step wrote).
            raise UncertainAnswer from None
        observed = common.observed_ids(entries)
        if observed is None:
            raise UncertainAnswer
        if observed != list(track_ids):
            raise NotWritten("playlist_mismatch")
        result = CallResult(
            playlist_id, {"snapshot_id": common.snapshot_of(snapshot), "count": len(track_ids)}
        )
        written.append(result)
        return result

    done = await perform(run, request, call, keyring=keyring)
    if done.external_id != playlist_id:
        raise JobFailure("internal")
    return written[0] if written else CallResult(playlist_id, {"count": len(track_ids)})


# --- the reconciler ----------------------------------------------------------------------------


def _call_over(operation: Operation) -> bool:
    """The call returned, or its bound and margin passed (``actionable_at``)."""
    if operation.call_returned:
        return True
    if operation.pending_at is None:
        return False
    bound = operation.bound_seconds
    wait = (FALLBACK_BOUND_SECONDS if bound is None else bound) + CALL_MAY_RUN_MARGIN_SECONDS
    return datetime.now(UTC) >= operation.pending_at + timedelta(seconds=wait)


async def _reconcile(check: ReconcileInput) -> Reconciliation:
    operation = check.operation
    context = operation.context
    purpose = spotify_purpose(operation.kind, operation.idempotency_key)
    if purpose is None or (purpose == "final") != (operation.class_id is not None):
        return INCONCLUSIVE
    playlist_id = context.get("playlist_id")
    stored = context.get("uris_hash")
    if not isinstance(playlist_id, str) or common.SPOTIFY_ID.fullmatch(playlist_id) is None:
        return INCONCLUSIVE
    if not isinstance(stored, str) or check.keyring is None:
        return INCONCLUSIVE
    try:
        opened = await open_connection(check.factory, check.keyring, check.ctx, "spotify")
        found = await common.call(
            check.factory,
            check.keyring,
            check.ctx,
            opened,
            lambda c: spotify_web.get_playlist(c, playlist_id),
        )
        if not common.owned(found, playlist_id, opened.account):
            return INCONCLUSIVE  # not ours to read, or no longer private: the user decides
        entries = await common.call(
            check.factory,
            check.keyring,
            check.ctx,
            opened,
            lambda c: spotify_web.playlist_items(c, playlist_id),
        )
    except (ConnectionFailure, OutboundBlocked):
        return INCONCLUSIVE
    observed = common.observed_ids(entries)
    if observed is None:
        return INCONCLUSIVE
    complete = [track_id for track_id in observed if track_id is not None]
    if len(complete) == len(observed) and common.ids_hash(complete).hex() == stored:
        return Reconciliation("succeeded", playlist_id)
    # Readable and different: the write is absolute, so this is final once nothing of the call
    # can still arrive; before that a late request could still make it equal.
    return Reconciliation("failed") if _call_over(operation) else INCONCLUSIVE


# --- on success --------------------------------------------------------------------------------

_POOL_WRITTEN = text(
    "UPDATE spotify_pool SET last_hash = :hash, last_written_at = now(), updated_at = now() "
    "WHERE owner_id = :owner_id AND id = :pool_id AND playlist_id = :playlist_id"
)
_VERIFIED = text(
    "UPDATE class_playlists SET state = 'verified', content_hash = :hash, "
    "track_count = :count, updated_at = now() "
    "WHERE owner_id = :owner_id AND class_id = :class_id AND playlist_id = :playlist_id "
    "AND detached_at IS NULL"
)


async def _on_succeeded(db: AsyncSession, owner_id: UUID, operation: Operation) -> None:
    context = operation.context
    purpose = spotify_purpose(operation.kind, operation.idempotency_key)
    playlist_id = context.get("playlist_id")
    count = context.get("count")
    try:
        digest = bytes.fromhex(str(context["uris_hash"]))
        if (
            purpose is None
            or (purpose == "final") != (operation.class_id is not None)
            or operation.external_id != playlist_id
            or len(digest) != 32
            or isinstance(count, bool)
            or not isinstance(count, int)
            or not 0 <= count <= common.MAX_TRACKS
        ):
            raise ValueError("not a replace")
        if operation.class_id is None:
            params = {
                "owner_id": owner_id,
                "pool_id": UUID(str(context["pool_id"])),
                "playlist_id": playlist_id,
                "hash": digest,
            }
            await db.execute(_POOL_WRITTEN, params)
            return
    except (KeyError, ValueError):
        logger.warning("replaced playlist not recorded: unreadable operation", extra={"kind": KIND})
        return
    await db.execute(
        _VERIFIED,
        {
            "owner_id": owner_id,
            "class_id": operation.class_id,
            "playlist_id": playlist_id,
            "hash": digest,
            "count": count,
        },
    )


REPLACE: Final = OPERATION_KINDS.register(OperationKind(KIND, _reconcile, _on_succeeded))
