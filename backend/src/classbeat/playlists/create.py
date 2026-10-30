"""``spotify_create_playlist`` (ADR 0008 section 2): a new private, non-collaborative playlist in
the user's own Spotify account, as an external operation. Create only: the tracks are written by
``spotify_replace_items`` afterwards, so the id exists on a row between the two requests.

- **Key** ``<job_id>:pool-create`` or ``<job_id>:final-create``: one row per job, never one per
  user, so a later job (after ``pool_missing``, a revoked and given-again consent, or a superseded
  uncertain create) makes a new row with a new id and therefore a new ``ref``. Only the attempts
  of one job share a row, and they take a ``failed`` row over only when the create provably did
  not happen (refused before it was sent).
- **Request hash** over ``{kind, purpose, name, pool_id}`` (the pool) or ``{kind, purpose, name,
  content_hash}`` (the final playlist, the approved ``song_list`` revision's hash).
- **Context** ``{purpose, pool_id}`` or ``{purpose, revision_id, content_hash, name,
  spotify_user_id}``, plus the protocol's ``ref`` (12 hex characters of the row's id). The
  playlist's description carries ``classbeat ref <ref>``.
- **The call** is one request (``single_request``): ``POST /me/playlists`` through the library's
  ``create_playlist`` with no tracks, which sends ``public: false`` and ``collaborative: false``.
  A request refused before it left, or answered with a refusal (4xx), is ``failed``; anything
  else (dropped after sending, a 5xx, an answer without a valid id) is ``uncertain``.
- **On success**, in the transaction that records it (whoever does: the worker, its
  reconciliation, the user's Check again on a final row): pool, ``spotify_pool.playlist_id`` set
  while the pool row is still the one the create was for and still has no playlist; final, the
  ``class_playlists`` row (``created``) of the operation's own class and revision.
- **The reconciler** lists the user's playlists (at most 20 pages of 50) for exactly one owned
  by the connected user, not public, whose description names this row's ``ref``:

  ======================================================  ===================================
  exactly one                                             ``succeeded`` with its id
  none, every page read (fewer than 1000), the call      ``failed``
  returned and ``CLASSBEAT_SPOTIFY_LISTING_LAG_SECONDS``
  set and passed since
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
from mowl.errors import MowlError
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from classbeat.connections import ConnectionFailure, Credentials, OpenConnection, open_connection
from classbeat.crypto.keks import KeyRing
from classbeat.egress import OutboundBlocked
from classbeat.jobs import JobFailure, JobRun
from classbeat.operations import (
    INCONCLUSIVE,
    OPERATION_KINDS,
    CallResult,
    Operation,
    OperationKind,
    OperationRequest,
    ReconcileInput,
    Reconciliation,
    UncertainAnswer,
    perform_for,
    request_hash,
)
from classbeat.operations.kinds import REF_KEY, SPOTIFY_CREATE_KIND, ref_of, spotify_purpose
from classbeat.operations.requests import Guard
from classbeat.playlists import common

logger = logging.getLogger(__name__)

KIND: Final = SPOTIFY_CREATE_KIND
# One request (30 s), plus a refresh before it and one after a 401 (10 s each), with room.
TIMEOUT_SECONDS: Final = 60.0


class _AnswerUnreadable(Exception):
    """The library refused Spotify's answer to the create (no valid id): the playlist may exist.
    Not the library's error type, so it is never taken for a refusal (``uncertain``)."""


def pool_key(job_id: UUID) -> str:
    return f"{job_id}:pool-create"


def final_key(job_id: UUID) -> str:
    return f"{job_id}:final-create"


def pool_request(job_id: UUID, pool_id: UUID, guard: Guard) -> OperationRequest:
    return OperationRequest(
        kind=KIND,
        idempotency_key=pool_key(job_id),
        request_hash=request_hash(
            KIND, purpose="pool", name=common.POOL_NAME, pool_id=str(pool_id)
        ),
        timeout_seconds=TIMEOUT_SECONDS,
        guard=guard,
        single_request=True,
        context={"purpose": "pool", "pool_id": str(pool_id)},
        with_ref=True,
    )


def final_request(
    job_id: UUID,
    class_id: UUID,
    revision_id: UUID,
    content_hash: bytes,
    name: str,
    account: str,
    guard: Guard,
) -> OperationRequest:
    if len(content_hash) != 32:
        raise JobFailure("invalid_input")
    name = common.check_name(name)
    return OperationRequest(
        kind=KIND,
        idempotency_key=final_key(job_id),
        request_hash=request_hash(
            KIND, purpose="final", name=name, content_hash=content_hash.hex()
        ),
        timeout_seconds=TIMEOUT_SECONDS,
        guard=guard,
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


def _create(name: str, about: str) -> Any:
    def fn(creds: Credentials) -> str:
        try:
            return spotify_web.create_playlist(creds, name, (), about)
        except MowlError as exc:
            if exc.__cause__ is None:
                # No HTTP cause: the library refused the answer (no valid id) or the token
                # before sending. The first may have created the playlist: never a refusal.
                raise _AnswerUnreadable from None
            raise

    return fn


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
            _create(name, about),
            write=True,
            single_request=True,  # one POST: a refusal proves it had no effect
        )
        if not isinstance(answer, str) or common.SPOTIFY_ID.fullmatch(answer) is None:
            raise UncertainAnswer
        return CallResult(answer)

    done = await perform_for(run, request, call, keyring=keyring)
    return common.check_playlist_id(done.external_id)


# --- the reconciler ----------------------------------------------------------------------------


def _lag_passed(operation: Operation) -> bool:
    lag = common.runtime().listing_lag_seconds
    if lag is None or not operation.call_returned or operation.call_ended_at is None:
        return False
    return datetime.now(UTC) >= operation.call_ended_at + timedelta(seconds=lag)


def _purpose_fits(operation: Operation) -> bool:
    purpose = spotify_purpose(operation.kind, operation.idempotency_key)
    return purpose is not None and (purpose == "final") == (operation.class_id is not None)


async def _reconcile(check: ReconcileInput) -> Reconciliation:
    operation = check.operation
    ref = operation.context.get(REF_KEY)
    if operation.kind != KIND or not _purpose_fits(operation) or ref != ref_of(operation.id):
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
            and playlist.get("public") is False
            and ref in common.refs_in(playlist.get("description"))
        ):
            found.append(playlist_id)
    if len(found) == 1:
        return Reconciliation("succeeded", found[0])
    if not found and _lag_passed(operation):
        return Reconciliation("failed")
    return INCONCLUSIVE


# --- on success --------------------------------------------------------------------------------

_POOL_PLAYLIST = text(
    "UPDATE spotify_pool SET playlist_id = :playlist_id, updated_at = now() "
    "WHERE owner_id = :owner_id AND id = :pool_id AND playlist_id IS NULL"
)
# The class playlist, from the operation row and the ``song_list`` revision **of the operation's
# own class** with the stored hash, so it can only ever name one owner's class, revision and
# operation. ``ON CONFLICT DO NOTHING``: idempotent (the live-playlist index).
_CLASS_PLAYLIST = text(
    "INSERT INTO class_playlists (owner_id, class_id, revision_id, playlist_id, spotify_user_id, "
    "name, state, create_operation_id) "
    "SELECT o.owner_id, o.class_id, r.id, :playlist_id, :spotify_user_id, :name, 'created', o.id "
    "FROM external_operations o JOIN class_revisions r ON r.owner_id = o.owner_id "
    "AND r.class_id = o.class_id AND r.id = :revision_id "
    "WHERE o.owner_id = :owner_id AND o.id = :operation_id AND o.kind = :kind "
    "AND o.state = 'succeeded' AND o.external_id = :playlist_id AND o.class_id = :class_id "
    "AND r.kind = 'song_list' AND r.content_hash = :content_hash "
    "ON CONFLICT DO NOTHING"
)


async def _on_succeeded(db: AsyncSession, owner_id: UUID, operation: Operation) -> None:
    playlist_id = operation.external_id
    if (
        operation.kind != KIND
        or not _purpose_fits(operation)
        or not isinstance(playlist_id, str)
        or common.SPOTIFY_ID.fullmatch(playlist_id) is None
    ):
        logger.warning("created playlist not recorded: not a create", extra={"kind": KIND})
        return
    context = operation.context
    try:
        if operation.class_id is None:
            await db.execute(
                _POOL_PLAYLIST,
                {
                    "owner_id": owner_id,
                    "pool_id": UUID(str(context["pool_id"])),
                    "playlist_id": playlist_id,
                },
            )
            return
        account = context["spotify_user_id"]
        name = common.check_name(context["name"])
        if not isinstance(account, str) or not 1 <= len(account) <= 100:
            raise ValueError("no Spotify user id")
        params = {
            "owner_id": owner_id,
            "operation_id": operation.id,
            "kind": KIND,
            "class_id": operation.class_id,
            "revision_id": UUID(str(context["revision_id"])),
            "content_hash": bytes.fromhex(str(context["content_hash"])),
            "playlist_id": playlist_id,
            "spotify_user_id": account,
            "name": name,
        }
    except (KeyError, ValueError, JobFailure):
        logger.warning("created playlist not recorded: unreadable operation", extra={"kind": KIND})
        return
    await db.execute(_CLASS_PLAYLIST, params)


CREATE: Final = OPERATION_KINDS.register(OperationKind(KIND, _reconcile, _on_succeeded))
