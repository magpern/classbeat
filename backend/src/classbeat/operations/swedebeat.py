"""``mowl_attach_swedebeat_mix`` (ADR 0016 section 3, A2): wrap one of the user's Swedebeat mixes in
their MOWL account, as adding the mix in the MOWL app would.

- **Listing.** ``parse_mixes`` turns MOWL's answer to ``POST v1/swedebeat/Playlists`` (the
  library's ``swedebeat_mixes``) into ``MixEntry`` values; the web route, the attach's own-listing
  check and the reconciler all read through it, so an entry is judged the same way everywhere.
- **Key** ``swedebeat:<mix_id>`` and **request hash** over ``{kind, source, source_id}``: every job
  of the user on that mix finds the same row. The mix id is MOWL's opaque id, held to the shape of
  the ``mowl_playlist_links`` check (``[0-9A-Za-z_-]{1,64}``): another user's id, a made-up id or a
  URL is never attached, because the id must also be in the caller's **own fresh listing** before
  the write (``invalid_input`` otherwise, nothing sent).
- **The call** is the library's ``mowl.api.attach_swedebeat`` (one ``PUT``), through
  ``classbeat.connections``. MOWL answers with its ``PlaylistID``, the operation's ``external_id``;
  on success, in the same transaction, the user's ``mowl_playlist_links`` row (``source =
  'swedebeat'``, ``ON CONFLICT DO NOTHING``). A repeated attach for an existing link is a no-op: the
  stored link answers and nothing is sent.
- **The reconciler** lists the user's mixes again and takes the ``PlaylistID`` the entry of this mix
  carries once MOWL has wrapped it. Whether MOWL's real listing carries that field is unverified
  until the owner's live check (ADR 0016 section 10); an entry without it, a failed listing or a
  listing that cannot show the mix leaves the row ``uncertain`` (inconclusive), and the user
  decides. A missing mix is never proof that the attach did not happen, so the reconciler never says
  ``failed``.
- **Indexing** is a separate, read-only wait after the operation (``wait_indexed``): MOWL processes
  a new mix before it can be read. A wait that fails does not undo the attach (the link stays).

The id returned by ``attach_swedebeat_mix`` is not guarded: a caller re-reads the link under
``hold`` in the transaction that uses it, as ``design.music`` does for a Spotify playlist.
"""

import asyncio
import re
from dataclasses import dataclass
from typing import Any, Final, cast
from uuid import UUID

from mowl import api as mowl_api
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from classbeat.agent.schemas import clean_text
from classbeat.connections import (
    ConnectionFailure,
    OpenConnection,
    call_opened,
    hold,
    open_connection,
)
from classbeat.crypto.keks import KeyRing
from classbeat.egress import OutboundBlocked
from classbeat.jobs import JobFailure, JobRun
from classbeat.operations.attach import MAX_PLAYLIST_ID, answered_playlist_id, playlist_id_of
from classbeat.operations.kinds import (
    ATTACH_SWEDEBEAT_KIND,
    INCONCLUSIVE,
    OPERATION_KINDS,
    Operation,
    OperationKind,
    ReconcileInput,
    Reconciliation,
    request_hash,
)
from classbeat.operations.protocol import CallResult, perform
from classbeat.operations.requests import Guard, OperationRequest

KIND: Final = ATTACH_SWEDEBEAT_KIND
SOURCE: Final = "swedebeat"
KEY_PREFIX: Final = f"{SOURCE}:"
MIX_ID: Final = re.compile(r"^[0-9A-Za-z_-]{1,64}$")  # the mowl_playlist_links check
MAX_NAME: Final = 200
MAX_MIXES: Final = 500
ATTACH_TIMEOUT_SECONDS: Final = 60.0  # one PUT; the library waits at most 30 s for it
INDEX_POLL_SECONDS: Final = 2.0  # as the library's ``wait_indexed``
INDEX_TIMEOUT_SECONDS: Final = 180.0

_LINK = text(
    "SELECT playlist_id FROM mowl_playlist_links "
    "WHERE owner_id = :owner_id AND source = 'swedebeat' AND source_id = :source_id"
)
_INSERT_LINK = text(
    "INSERT INTO mowl_playlist_links (id, owner_id, source, source_id, playlist_id) "
    "VALUES (gen_random_uuid(), :owner_id, 'swedebeat', :source_id, :playlist_id) "
    "ON CONFLICT (owner_id, source, source_id) DO NOTHING"
)


@dataclass(frozen=True, slots=True)
class MixEntry:
    """One of the user's mixes as MOWL lists it. ``playlist_id`` is the MOWL playlist that wraps it
    when the listing says so (the reconciler's evidence; never returned to the browser)."""

    mix_id: str
    name: str
    duration_seconds: int | None
    playlist_id: int | None = None


def parse_mixes(answer: object) -> list[MixEntry]:
    """The mixes in MOWL's listing. An entry whose id has not the shape of an id is left out; a
    name loses control characters and is cut to 200 characters; a duration that is not a
    non-negative number is ``None``. At most ``MAX_MIXES``; a repeated id keeps its first entry."""
    out: list[MixEntry] = []
    seen: set[str] = set()
    entries: list[Any] = answer if isinstance(answer, list) else []  # pyright: ignore[reportUnknownVariableType]
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        fields = cast(dict[str, Any], entry)
        mix_id = fields.get("ID")
        if not isinstance(mix_id, str) or not MIX_ID.fullmatch(mix_id) or mix_id in seen:
            continue
        seen.add(mix_id)
        name = fields.get("Name")
        out.append(
            MixEntry(
                mix_id,
                clean_text(name if isinstance(name, str) else "")[:MAX_NAME],
                _seconds(fields.get("DurationInMilliseconds")),
                _playlist_id(fields.get("PlaylistID")),
            )
        )
        if len(out) >= MAX_MIXES:
            break
    return out


def _seconds(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    if not 0 <= value <= 24 * 3600 * 1000:  # also NaN
        return None
    return int(value // 1000)


def _playlist_id(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or not 0 < value <= MAX_PLAYLIST_ID:
        return None
    return value


def check_mix_id(mix_id: str) -> str:
    if not isinstance(mix_id, str) or not MIX_ID.fullmatch(mix_id):  # pyright: ignore[reportUnnecessaryIsInstance]
        raise JobFailure("invalid_input")
    return mix_id


def idempotency_key(mix_id: str) -> str:
    return KEY_PREFIX + check_mix_id(mix_id)


def attach_request(mix_id: str, guard: Guard | None = None) -> OperationRequest:
    mix_id = check_mix_id(mix_id)
    return OperationRequest(
        kind=KIND,
        idempotency_key=idempotency_key(mix_id),
        request_hash=request_hash(KIND, source=SOURCE, source_id=mix_id),
        timeout_seconds=ATTACH_TIMEOUT_SECONDS,
        guard=guard,
        single_request=True,  # one PUT (mowl.api.attach_swedebeat)
    )


async def _write_link(db: AsyncSession, owner_id: UUID, operation: Operation) -> None:
    key, external_id = operation.idempotency_key, operation.external_id
    if operation.kind != KIND or not key.startswith(KEY_PREFIX) or external_id is None:
        raise ValueError("not a succeeded Swedebeat attach")
    await db.execute(
        _INSERT_LINK,
        {
            "owner_id": owner_id,
            "source_id": check_mix_id(key.removeprefix(KEY_PREFIX)),
            "playlist_id": playlist_id_of(external_id),
        },
    )


async def _reconcile(check: ReconcileInput) -> Reconciliation:
    operation = check.operation
    key = operation.idempotency_key
    if operation.kind != KIND or not key.startswith(KEY_PREFIX) or check.keyring is None:
        return INCONCLUSIVE
    mix_id = key.removeprefix(KEY_PREFIX)
    if MIX_ID.fullmatch(mix_id) is None:
        return INCONCLUSIVE
    try:
        opened = await open_connection(check.factory, check.keyring, check.ctx, "mowl")
        answer = await call_opened(
            check.factory, check.ctx, opened, lambda creds: mowl_api.swedebeat_mixes(creds)
        )
    except (ConnectionFailure, OutboundBlocked):
        return INCONCLUSIVE  # a failed listing proves nothing
    found = [m for m in parse_mixes(answer) if m.mix_id == mix_id and m.playlist_id is not None]
    if len(found) == 1 and found[0].playlist_id is not None:
        return Reconciliation("succeeded", str(found[0].playlist_id))
    return INCONCLUSIVE


ATTACH: Final = OPERATION_KINDS.register(OperationKind(KIND, _reconcile, _write_link))


async def linked_mix_playlist(run: JobRun, mix_id: str) -> int | None:
    """The user's stored MOWL playlist for this mix, if any."""
    async with run.fenced() as fs:
        return (
            await fs.db.execute(
                _LINK, {"owner_id": run.lease.owner_id, "source_id": check_mix_id(mix_id)}
            )
        ).scalar_one_or_none()


def _still(run: JobRun, opened: OpenConnection) -> Guard:
    async def guard(db: AsyncSession) -> bool:
        return await hold(db, run.ctx, opened)

    return guard


async def attach_swedebeat_mix(
    run: JobRun, keyring: KeyRing, mix_id: str, *, connection_id: UUID
) -> int:
    """The MOWL ``PlaylistID`` wrapping the user's Swedebeat mix ``mix_id``: the stored link, or
    the attach as an external operation (which stores the link).

    ``connection_id`` is the job's ``mowl_connection_id``: it must be the context's MOWL
    connection and still ``connected`` before any call (``not_connected``). Without a link the
    user's own fresh listing is read first and must hold the id (``invalid_input`` otherwise,
    nothing written). Raises ``JobFailure`` with the category, and what ``perform`` raises."""
    mix_id = check_mix_id(mix_id)
    linked = await linked_mix_playlist(run, mix_id)
    if linked is not None:
        return linked
    if run.ctx.connection("mowl") != connection_id:
        raise JobFailure("not_connected")
    opened = await open_connection(run.factory, keyring, run.ctx, "mowl")
    if opened.id != connection_id:
        raise JobFailure("not_connected")
    listing = await call_opened(
        run.factory, run.ctx, opened, lambda creds: mowl_api.swedebeat_mixes(creds)
    )
    if all(m.mix_id != mix_id for m in parse_mixes(listing)):
        raise JobFailure("invalid_input")

    async def call() -> CallResult:
        answer = await call_opened(
            run.factory,
            run.ctx,
            opened,
            lambda creds: mowl_api.attach_swedebeat(creds, mix_id),
            single_request=True,  # one PUT: a refusal proves it had no effect
        )
        return CallResult(str(answered_playlist_id(answer)))

    done = await perform(run, attach_request(mix_id, _still(run, opened)), call, keyring=keyring)
    return playlist_id_of(done.external_id)


@dataclass(frozen=True, slots=True)
class IndexedMix:
    track_id: int
    duration_seconds: float


async def wait_indexed(
    run: JobRun,
    keyring: KeyRing,
    playlist_id: int,
    *,
    connection_id: UUID,
    poll_seconds: float = INDEX_POLL_SECONDS,
    timeout_seconds: float = INDEX_TIMEOUT_SECONDS,
) -> IndexedMix:
    """Read the wrapped mix's track until MOWL has processed it (read-only; a checkpoint before
    every read, so a cancel or a lost lease stops it). A processing error or the timeout ends it
    with the retryable ``upstream_unavailable``."""
    if run.ctx.connection("mowl") != connection_id:
        raise JobFailure("not_connected")
    opened = await open_connection(run.factory, keyring, run.ctx, "mowl")
    if opened.id != connection_id:
        raise JobFailure("not_connected")
    waited = 0.0
    while True:
        await run.checkpoint()
        answer = await call_opened(
            run.factory,
            run.ctx,
            opened,
            lambda creds: mowl_api.playlist_tracks(creds, playlist_id),
        )
        track: Any = answer[0] if isinstance(answer, list) and answer else None  # pyright: ignore[reportUnknownVariableType]
        if not isinstance(track, dict):
            raise JobFailure("upstream_unavailable")
        fields = cast(dict[str, Any], track)
        if fields.get("IndexStatus") == "Track indexing done":
            track_id, length = fields.get("TrackID"), fields.get("Duration")
            if (
                isinstance(track_id, int)
                and not isinstance(track_id, bool)
                and isinstance(length, int | float)
                and not isinstance(length, bool)
                and length > 0
            ):
                return IndexedMix(track_id, float(length) / 1000)
            raise JobFailure("upstream_unavailable")
        if fields.get("IndexError") or waited >= timeout_seconds:
            raise JobFailure("upstream_unavailable")
        await asyncio.sleep(poll_seconds)
        waited += poll_seconds
