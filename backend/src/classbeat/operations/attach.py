"""``mowl_attach_spotify_playlist`` (ADR 0006 section 3): wrap a Spotify playlist in a MOWL
playlist, the one external-operation kind of M3a.

- **Key** ``spotify:<source_id>`` and **request hash** over ``{kind, source, source_id}``
  (ADR 0005 section 7): every job of the user on that playlist finds the same row, whoever asks.
- **A playlist classbeat rewrites** (the pool, #1535) is attached again after **every** write:
  MOWL's wrapper is a snapshot of the Spotify playlist at attach time and never follows a later
  edit, and each attach answers a new ``PlaylistID``. ``attach_written_playlist`` keys it per
  write, ``spotify:<source_id>:<w16>`` (``w16``: 16 hex characters of the SHA-256 of the
  Spotify write's own key, also in the request hash), so a retry of the same write reuses its
  attach while a new write attaches anew.
- **The call** is the library's ``mowl.api.attach_spotify`` with the user's MOWL credentials,
  through ``classbeat.connections`` (in the worker, behind the outbound allowlist of ADR 0006
  section 9). MOWL answers with its ``PlaylistID``, which becomes the operation's
  ``external_id``.
- **On success**, in the same transaction, the user's ``mowl_playlist_links`` row, which
  always names the **newest** wrapper of the playlist (an upsert; a changed playlist id gets a
  new row id, so a revision that named the older link no longer finds it and Save refuses
  ``music_unlinked`` instead of saving onto other tracks). Later jobs on a playlist classbeat
  does not write need no write.
- **The reconciler** always returns inconclusive: MOWL has no read that maps a Spotify id to a
  MOWL playlist. An uncertain ``spotify:<source_id>`` attach waits for the user; at worst **Do it
  anyway** adds a second wrapper to their own MOWL music. A per-write one is never offered to the
  user (``classbeat.operations.user``): the next write attaches under its own key. (#105 once
  noted that re-attaching returns the same ``PlaylistID``; the pre-prod stack showed a new one
  with the current tracks, #1535.)

``attach_spotify_playlist`` is what the ``design_draft`` pipeline (D1) calls for its music step:
the stored link if there is one, else the attach.

**The id it returns is not guarded** (#117 item 1): the stored link is read without the
connection's share lock, so a reconnect in place as another MOWL account right after the read
(which deletes the links, ADR 0006 section 3) leaves the caller with the old account's playlist
id. No write is duplicated by that, but a caller must re-read the link under ``hold`` in the
transaction that uses it for a read or write against MOWL, as D1's ``design.music._held_link``
does; M3b's saves must do the same. ``attach_written_playlist`` re-points the link itself, under
``hold``, only while its own succeeded row still exists (a reconnect deletes both).
"""

import hashlib
import re
from collections.abc import Awaitable, Callable
from typing import Any, Final
from uuid import UUID

from mowl import api as mowl_api
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from classbeat.connections import OpenConnection, call_opened, hold, open_connection
from classbeat.crypto.keks import KeyRing
from classbeat.jobs import JobFailure, JobRun
from classbeat.operations.kinds import (
    ATTACH_KIND,
    INCONCLUSIVE,
    OPERATION_KINDS,
    Operation,
    OperationKind,
    ReconcileInput,
    Reconciliation,
    request_hash,
)
from classbeat.operations.protocol import CallResult, UncertainAnswer, perform
from classbeat.operations.requests import Guard, OperationRequest

KIND: Final = ATTACH_KIND
SOURCE: Final = "spotify"
KEY_PREFIX: Final = f"{SOURCE}:"
SPOTIFY_ID: Final = re.compile(r"^[0-9A-Za-z]{22}$")  # the mowl_playlist_links.source_id check
# ``spotify:<source_id>`` or, per write, ``spotify:<source_id>:<w16>``.
_KEY: Final = re.compile(r"spotify:([0-9A-Za-z]{22})(?::[0-9a-f]{16})?")
# ``mowl.api.attach_spotify`` waits up to 120 s (MOWL takes about 10 s); the lease outlives it.
ATTACH_TIMEOUT_SECONDS: Final = 120.0
MAX_PLAYLIST_ID: Final = 2**63 - 1  # bigint

_LINK = text(
    "SELECT playlist_id FROM mowl_playlist_links "
    "WHERE owner_id = :owner_id AND source = :source AND source_id = :source_id"
)
# The newest wrapper wins; a changed playlist id is a new link (a new row id), the same one keeps
# its row.
_POINT_LINK = text(
    "INSERT INTO mowl_playlist_links (id, owner_id, source, source_id, playlist_id) "
    "VALUES (gen_random_uuid(), :owner_id, :source, :source_id, :playlist_id) "
    "ON CONFLICT (owner_id, source, source_id) DO UPDATE SET id = gen_random_uuid(), "
    "playlist_id = EXCLUDED.playlist_id, created_at = now() "
    "WHERE mowl_playlist_links.playlist_id <> EXCLUDED.playlist_id"
)
# An attach row that still stands (a reconnect deletes every attach row and link, ADR 0006
# section 3).
_SUCCEEDED = text(
    "SELECT external_id FROM external_operations WHERE owner_id = :owner_id AND kind = :kind "
    "AND idempotency_key = :key AND state = 'succeeded'"
)


def check_source_id(source_id: str) -> str:
    if not isinstance(source_id, str) or not SPOTIFY_ID.fullmatch(source_id):  # pyright: ignore[reportUnnecessaryIsInstance]
        raise JobFailure("invalid_input")
    return source_id


def idempotency_key(source_id: str) -> str:
    return KEY_PREFIX + check_source_id(source_id)


def write_digest(write_key: str) -> str:
    """``w16`` of a per-write key: 16 hex characters of the SHA-256 of the write's own key."""
    if not isinstance(write_key, str) or not 1 <= len(write_key) <= 120:  # pyright: ignore[reportUnnecessaryIsInstance]
        raise JobFailure("internal")
    return hashlib.sha256(write_key.encode("utf-8")).hexdigest()[:16]


def attach_request(
    source_id: str, guard: Guard | None = None, *, write_key: str | None = None
) -> OperationRequest:
    """The attach of ``source_id``; with ``write_key``, the attach after that Spotify write."""
    source_id = check_source_id(source_id)
    key, fields = idempotency_key(source_id), {"source": SOURCE, "source_id": source_id}
    if write_key is not None:
        digest = write_digest(write_key)
        key, fields = f"{key}:{digest}", {**fields, "write": digest}
    return OperationRequest(
        kind=KIND,
        idempotency_key=key,
        request_hash=request_hash(KIND, **fields),
        timeout_seconds=ATTACH_TIMEOUT_SECONDS,
        guard=guard,
        single_request=True,  # one PUT (mowl.api.attach_spotify)
    )


def playlist_id_of(external_id: str) -> int:
    value = int(external_id)
    if not 0 < value <= MAX_PLAYLIST_ID:
        raise ValueError("not a MOWL playlist id")
    return value


def answered_playlist_id(answer: object) -> int:
    """MOWL's ``PlaylistID`` from the attach answer; anything else is an uncertain answer (MOWL
    may have attached it)."""
    value: Any = answer.get("PlaylistID") if isinstance(answer, dict) else None  # pyright: ignore[reportUnknownMemberType]
    if not isinstance(value, int) or isinstance(value, bool) or not 0 < value <= MAX_PLAYLIST_ID:
        raise UncertainAnswer
    return value


async def _point_link(db: AsyncSession, owner_id: UUID, source_id: str, playlist_id: int) -> None:
    params = {"source": SOURCE, "source_id": source_id, "playlist_id": playlist_id}
    await db.execute(_POINT_LINK, {"owner_id": owner_id, **params})


async def _write_link(db: AsyncSession, owner_id: UUID, operation: Operation) -> None:
    found = _KEY.fullmatch(operation.idempotency_key)
    if found is None or operation.external_id is None:
        raise ValueError("not a succeeded attach")
    await _point_link(db, owner_id, found[1], playlist_id_of(operation.external_id))


async def _reconcile(_: ReconcileInput) -> Reconciliation:
    """No read maps a Spotify id to a MOWL playlist: always inconclusive (ADR 0006 section 3)."""
    return INCONCLUSIVE


ATTACH: Final = OPERATION_KINDS.register(OperationKind(KIND, _reconcile, _write_link))


async def linked_playlist(run: JobRun, source_id: str) -> int | None:
    """The user's stored MOWL playlist for this Spotify playlist, if any."""
    async with run.fenced() as fs:
        return (
            await fs.db.execute(
                _LINK,
                {"owner_id": run.lease.owner_id, "source": SOURCE, "source_id": source_id},
            )
        ).scalar_one_or_none()


def _still(run: JobRun, opened: OpenConnection) -> Guard:
    async def guard(db: AsyncSession) -> bool:
        return await hold(db, run.ctx, opened)

    return guard


async def _opened(run: JobRun, keyring: KeyRing, connection_id: UUID) -> OpenConnection:
    """The job's MOWL connection: the context's, connected (``not_connected`` otherwise)."""
    if run.ctx.connection("mowl") != connection_id:
        raise JobFailure("not_connected")
    opened = await open_connection(run.factory, keyring, run.ctx, "mowl")
    if opened.id != connection_id:
        raise JobFailure("not_connected")
    return opened


def _call(
    run: JobRun, opened: OpenConnection, source_id: str
) -> Callable[[], Awaitable[CallResult]]:
    async def call() -> CallResult:
        answer = await call_opened(
            run.factory,
            run.ctx,
            opened,
            lambda creds: mowl_api.attach_spotify(creds, source_id),
            single_request=True,  # one PUT: a refusal proves it had no effect
        )
        return CallResult(str(answered_playlist_id(answer)))

    return call


async def attach_spotify_playlist(
    run: JobRun, keyring: KeyRing, source_id: str, *, connection_id: UUID
) -> int:
    """The MOWL ``PlaylistID`` for the Spotify playlist ``source_id``: the stored link, or the
    attach as an external operation (which stores the link).

    ``connection_id`` is the job's ``mowl_connection_id``: it must be the context's MOWL
    connection and still ``connected`` before any call (``not_connected`` otherwise, ADR 0006
    section 9). Raises ``JobFailure`` with the category, and what ``perform`` raises. The id is
    read unguarded: re-read the link under ``hold`` before using it (module docstring).
    """
    source_id = check_source_id(source_id)
    linked = await linked_playlist(run, source_id)
    if linked is not None:
        return linked
    opened = await _opened(run, keyring, connection_id)
    request = attach_request(source_id, _still(run, opened))
    done = await perform(run, request, _call(run, opened, source_id), keyring=keyring)
    return playlist_id_of(done.external_id)


async def attach_written_playlist(
    run: JobRun, keyring: KeyRing, source_id: str, write_key: str, *, connection_id: UUID
) -> int:
    """The MOWL ``PlaylistID`` of a fresh wrapper of ``source_id`` after the Spotify write whose
    key is ``write_key`` (the module docstring): never the stored link. A retry of the same write
    reuses its attach. Then, in one fenced transaction holding the connection, the attach row is
    read again and the link pointed at its wrapper (``not_connected`` if a reconnect deleted it),
    so the music step's ``_held_link`` finds exactly this id. Raises as ``attach_spotify_playlist``.
    """
    source_id = check_source_id(source_id)
    opened = await _opened(run, keyring, connection_id)
    request = attach_request(source_id, _still(run, opened), write_key=write_key)
    done = await perform(run, request, _call(run, opened, source_id), keyring=keyring)
    playlist_id = playlist_id_of(done.external_id)
    async with run.fenced() as fs:
        if not await hold(fs.db, run.ctx, opened):
            raise JobFailure("not_connected")
        owner = run.lease.owner_id
        params = {"owner_id": owner, "kind": KIND, "key": request.idempotency_key}
        if (await fs.db.execute(_SUCCEEDED, params)).scalar_one_or_none() != done.external_id:
            raise JobFailure("not_connected")
        await _point_link(fs.db, owner, source_id, playlist_id)
    return playlist_id
