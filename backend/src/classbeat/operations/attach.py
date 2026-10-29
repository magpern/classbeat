"""``mowl_attach_spotify_playlist`` (ADR 0006 section 3): wrap a Spotify playlist in a MOWL
playlist, the one external-operation kind of M3a.

- **Key** ``spotify:<source_id>`` and **request hash** over ``{kind, source, source_id}``
  (ADR 0005 section 7): every job of the user on that playlist finds the same row, whoever asks.
- **The call** is the library's ``mowl.api.attach_spotify`` with the user's MOWL credentials,
  through ``classbeat.connections`` (in the worker, behind the outbound allowlist of ADR 0006
  section 9). MOWL answers with its ``PlaylistID``, which becomes the operation's
  ``external_id``.
- **On success**, in the same transaction, the user's ``mowl_playlist_links`` row
  (``ON CONFLICT DO NOTHING``), so later jobs on the same playlist need no write.
- **The reconciler** always returns inconclusive: MOWL has no read that maps a Spotify id to a
  MOWL playlist known to be idempotent under concurrency (the one sequential observation that
  re-attaching returns the same ``PlaylistID`` is not verified live, #105). An uncertain attach
  waits for the user; at worst **Do it anyway** adds a second wrapper to their own MOWL music.

``attach_spotify_playlist`` is what the ``design_draft`` pipeline (D1) calls for its music step:
the stored link if there is one, else the attach.

**The id it returns is not guarded** (#117 item 1): the stored link is read without the
connection's share lock, so a reconnect in place as another MOWL account right after the read
(which deletes the links, ADR 0006 section 3) leaves the caller with the old account's playlist
id. No write is duplicated by that, but a caller must re-read the link under ``hold`` in the
transaction that uses it for a read or write against MOWL, as D1's ``design.music._held_link``
does; M3b's saves must do the same.
"""

import re
from typing import Any, Final
from uuid import UUID

from mowl import api as mowl_api
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from classbeat.connections import OpenConnection, call_opened, hold, open_connection
from classbeat.crypto.keks import KeyRing
from classbeat.jobs import JobFailure, JobRun
from classbeat.operations.kinds import (
    INCONCLUSIVE,
    OPERATION_KINDS,
    OperationKind,
    ReconcileInput,
    Reconciliation,
    request_hash,
)
from classbeat.operations.protocol import (
    CallResult,
    Guard,
    OperationRequest,
    UncertainAnswer,
    perform,
)

KIND: Final = "mowl_attach_spotify_playlist"
SOURCE: Final = "spotify"
KEY_PREFIX: Final = f"{SOURCE}:"
SPOTIFY_ID: Final = re.compile(r"^[0-9A-Za-z]{22}$")  # the mowl_playlist_links.source_id check
# ``mowl.api.attach_spotify`` waits up to 120 s (MOWL takes about 10 s); the lease outlives it.
ATTACH_TIMEOUT_SECONDS: Final = 120.0
MAX_PLAYLIST_ID: Final = 2**63 - 1  # bigint

_LINK = text(
    "SELECT playlist_id FROM mowl_playlist_links "
    "WHERE owner_id = :owner_id AND source = :source AND source_id = :source_id"
)
_INSERT_LINK = text(
    "INSERT INTO mowl_playlist_links (id, owner_id, source, source_id, playlist_id) "
    "VALUES (gen_random_uuid(), :owner_id, :source, :source_id, :playlist_id) "
    "ON CONFLICT (owner_id, source, source_id) DO NOTHING"
)


def check_source_id(source_id: str) -> str:
    if not isinstance(source_id, str) or not SPOTIFY_ID.fullmatch(source_id):  # pyright: ignore[reportUnnecessaryIsInstance]
        raise JobFailure("invalid_input")
    return source_id


def idempotency_key(source_id: str) -> str:
    return KEY_PREFIX + check_source_id(source_id)


def attach_request(source_id: str, guard: Guard | None = None) -> OperationRequest:
    source_id = check_source_id(source_id)
    return OperationRequest(
        kind=KIND,
        idempotency_key=idempotency_key(source_id),
        request_hash=request_hash(KIND, source=SOURCE, source_id=source_id),
        timeout_seconds=ATTACH_TIMEOUT_SECONDS,
        guard=guard,
        single_request=True,  # one PUT (mowl.api.attach_spotify)
    )


def playlist_id_of(external_id: str) -> int:
    value = int(external_id)
    if not 0 < value <= MAX_PLAYLIST_ID:
        raise ValueError("not a MOWL playlist id")
    return value


def _answered_playlist_id(answer: object) -> int:
    """MOWL's ``PlaylistID`` from the attach answer; anything else is an uncertain answer (MOWL
    may have attached it)."""
    value: Any = answer.get("PlaylistID") if isinstance(answer, dict) else None  # pyright: ignore[reportUnknownMemberType]
    if not isinstance(value, int) or isinstance(value, bool) or not 0 < value <= MAX_PLAYLIST_ID:
        raise UncertainAnswer
    return value


async def _write_link(db: AsyncSession, owner_id: UUID, key: str, external_id: str) -> None:
    if not key.startswith(KEY_PREFIX):
        raise ValueError("not an attach key")
    await db.execute(
        _INSERT_LINK,
        {
            "owner_id": owner_id,
            "source": SOURCE,
            "source_id": key.removeprefix(KEY_PREFIX),
            "playlist_id": playlist_id_of(external_id),
        },
    )


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
    if run.ctx.connection("mowl") != connection_id:
        raise JobFailure("not_connected")
    opened = await open_connection(run.factory, keyring, run.ctx, "mowl")
    if opened.id != connection_id:
        raise JobFailure("not_connected")

    async def call() -> CallResult:
        answer = await call_opened(
            run.factory,
            run.ctx,
            opened,
            lambda creds: mowl_api.attach_spotify(creds, source_id),
            single_request=True,  # one PUT: a refusal proves it had no effect
        )
        return CallResult(str(_answered_playlist_id(answer)))

    request = attach_request(source_id, _still(run, opened))
    done = await perform(run, request, call, keyring=keyring)
    return playlist_id_of(done.external_id)
