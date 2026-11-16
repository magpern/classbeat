"""The external-operation kinds of a studio output (ADR 0036 section 6, ADR 0005 section 7, SR C7):
``swedebeat_create_playlist``, ``swedebeat_publish_playlist``, ``swedebeat_merge_publish`` and
``mowl_attach_studio_mix``. Each carries its class; its key is the approval's
(``output_key_policy``: ``out:<class>:<revision>:<output>:<attempt>:<step>``).

- **Request hash** (``output_request``): the canonical JSON of ``{kind, draft (the approved
  content_hash), output, attempt, step, the stored names and the exact body fields}``; for the
  merge the stored **render plan**, the tracks' analysed sha256s, ``X-Tracks`` and the name, never
  the MP3 bytes. The same key with another hash is refused ``internal`` (the protocol).
- **On success**, in the transaction that records it (``on_succeeded``), the returned id goes into
  the attempt's ``class_outputs`` row before any next step: the create's ``playlist_id``, the
  publish (the output ``done``), the merge's URL (``done``; its ``external_id`` is the URL's
  SHA-256, the URL is in the operation's ``result``) and the attach's ``PlaylistID`` (``done``,
  with the user's ``mowl_playlist_links`` row for the mix).
- **Reconcilers** (read-only, the job's one automatic check and the user's Check again):
  a **create** in doubt cannot be listed (``/playlists/shared`` lists published playlists only),
  so it is always inconclusive: it stays in doubt and the way on is Create another anyway; a
  **publish** in doubt reads **every page** of ``GET /playlists/shared``: listed is
  ``succeeded``, not listed (every page read) is ``failed``, because publishing the same id again
  is safe (one-way on an existing record, it creates none); a **merge** in doubt is never settled
  here (the MOWL listing by its unique name is shown to the user, never taken: ``outputs_view``);
  an **attach** in doubt is ``succeeded`` when the user's MOWL listing shows the ``PlaylistID``
  wrapping that mix. A failed read is inconclusive.
"""

import hashlib
import json
import logging
from collections.abc import Mapping
from typing import Any, Final
from uuid import UUID

from mowl import api as mowl_api
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from classbeat.connections import ConnectionFailure, call_opened, open_connection
from classbeat.connections.swedebeat_account import open_bearer
from classbeat.egress import OutboundBlocked
from classbeat.egress_gateway import MATCH_SECONDS
from classbeat.operations import (
    INCONCLUSIVE,
    OPERATION_KINDS,
    Operation,
    OperationKind,
    OperationRequest,
    ReconcileInput,
    Reconciliation,
    request_hash,
)
from classbeat.operations.attach import playlist_id_of
from classbeat.operations.requests import Guard
from classbeat.operations.swedebeat import MIX_ID, parse_mixes
from classbeat.studio.output_key_policy import OutputKey, parse
from classbeat.swedebeat_gateway import opener, writes
from classbeat.swedebeat_gateway.write_policy import is_mix_url, is_playlist_id
from classbeat.swedebeat_match import config as match_config
from classbeat.swedebeat_studio import outputs_rows

logger = logging.getLogger(__name__)

CREATE: Final = "swedebeat_create_playlist"
PUBLISH: Final = "swedebeat_publish_playlist"
MERGE: Final = "swedebeat_merge_publish"
ATTACH: Final = "mowl_attach_studio_mix"
KIND_OF_STEP: Final = {"create": CREATE, "publish": PUBLISH, "merge": MERGE, "attach": ATTACH}
CREATE_SECONDS: Final = MATCH_SECONDS  # the gateway fetches every track: "at least 120 s"
PUBLISH_SECONDS: Final = 60.0
ATTACH_SECONDS: Final = 60.0  # one PUT; the library waits at most 30 s
SHARED_SECONDS: Final = 60.0  # one page

_LINK = text(
    "INSERT INTO mowl_playlist_links (id, owner_id, source, source_id, playlist_id) "
    "VALUES (gen_random_uuid(), :owner_id, 'swedebeat', :source_id, :playlist_id) "
    "ON CONFLICT (owner_id, source, source_id) DO NOTHING"
)


def canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def url_digest(url: str) -> str:
    return hashlib.sha256(url.encode("utf-8")).hexdigest()


def output_request(
    okey: OutputKey,
    draft_hash: bytes,
    fields: Mapping[str, object],
    *,
    timeout_seconds: float,
    guard: Guard,
    context: Mapping[str, Any] | None = None,
) -> OperationRequest:
    """One step's request: its kind, the approval's key and the hash over everything it writes."""
    kind = KIND_OF_STEP[okey.step]
    return OperationRequest(
        kind=kind,
        idempotency_key=str(okey),
        request_hash=request_hash(
            kind,
            draft=draft_hash.hex(),
            output=okey.output,
            attempt=str(okey.attempt),
            step=okey.step,
            fields=canonical(dict(fields)),
        ),
        timeout_seconds=timeout_seconds,
        guard=guard,
        class_id=okey.class_id,
        context=dict(context or {}),
    )


def _key_of(operation: Operation, kind: str) -> OutputKey | None:
    found = parse(operation.idempotency_key)
    if operation.kind != kind or found is None or found.class_id != operation.class_id:
        return None
    return found if KIND_OF_STEP.get(found.step) == kind else None


async def _record(
    db: AsyncSession,
    owner_id: UUID,
    okey: OutputKey,
    *,
    state: str | None = None,
    external_id: str | None = None,
    patch: Mapping[str, Any],
) -> None:
    """Write the step's result into its own attempt's row (found by the key's parts)."""
    row_id = await _attempt_id(db, owner_id, okey)
    if row_id is None:
        logger.warning("output step recorded without its attempt", extra={"kind": okey.step})
        return
    await outputs_rows.update(
        db, owner_id, row_id, state=state, external_id=external_id, patch=patch
    )


_ATTEMPT = text(
    "SELECT id FROM class_outputs WHERE owner_id = :owner_id AND class_id = :class_id "
    "AND revision_id = :revision_id AND output = :output AND attempt = :attempt"
)


async def _attempt_id(db: AsyncSession, owner_id: UUID, okey: OutputKey) -> UUID | None:
    params = {
        "owner_id": owner_id,
        "class_id": okey.class_id,
        "revision_id": okey.revision_id,
        "output": okey.output,
        "attempt": okey.attempt,
    }
    return (await db.execute(_ATTEMPT, params)).scalar_one_or_none()


async def _created(db: AsyncSession, owner_id: UUID, operation: Operation) -> None:
    okey = _key_of(operation, CREATE)
    if okey is None or not is_playlist_id(operation.external_id):
        return
    await _record(db, owner_id, okey, patch={"playlist_id": operation.external_id})


async def _published(db: AsyncSession, owner_id: UUID, operation: Operation) -> None:
    okey = _key_of(operation, PUBLISH)
    if okey is None or not is_playlist_id(operation.external_id):
        return
    patch = {"published": True}
    await _record(db, owner_id, okey, state="done", external_id=operation.external_id, patch=patch)


async def _merged(db: AsyncSession, owner_id: UUID, operation: Operation) -> None:
    okey = _key_of(operation, MERGE)
    url = operation.result.get("url")
    if okey is None or not is_mix_url(url) or operation.external_id != url_digest(str(url)):
        return  # a Check-again confirm records the URL itself (``outputs_view``)
    await _record(db, owner_id, okey, state="done", external_id=str(url), patch={"mix_url": url})


async def _attached(db: AsyncSession, owner_id: UUID, operation: Operation) -> None:
    okey = _key_of(operation, ATTACH)
    mix_id = operation.context.get("mix_id")
    if okey is None or operation.external_id is None or not isinstance(mix_id, str):
        return
    if MIX_ID.fullmatch(mix_id) is None:
        return
    try:
        playlist_id = playlist_id_of(operation.external_id)
    except ValueError:
        return
    await db.execute(_LINK, {"owner_id": owner_id, "source_id": mix_id, "playlist_id": playlist_id})
    patch = {"mowl_playlist_id": playlist_id}
    await _record(db, owner_id, okey, state="done", external_id=str(playlist_id), patch=patch)


async def _never(_: ReconcileInput) -> Reconciliation:
    return INCONCLUSIVE  # a create in doubt cannot be listed; a merge is the user's to confirm


async def _publish_listed(check: ReconcileInput) -> Reconciliation:
    playlist_id = check.operation.context.get("playlist_id")
    key = match_config.current().swedebeat_gateway_key
    if _key_of(check.operation, PUBLISH) is None or not is_playlist_id(playlist_id):
        return INCONCLUSIVE
    if check.keyring is None or key is None:
        return INCONCLUSIVE
    try:
        opened = await open_bearer(check.factory, check.keyring, check.ctx)
        async with opener.open_client(SHARED_SECONDS * writes.SHARED_PAGES) as client:
            listed = await writes.list_shared(client, key, opened.bearer)
    except (ConnectionFailure, writes.WriteFailure, OutboundBlocked, ValueError):
        return INCONCLUSIVE  # a failed listing proves nothing
    if playlist_id in listed:
        return Reconciliation("succeeded", str(playlist_id))
    return Reconciliation("failed")  # every page read: publishing the same id again is safe


async def _attach_listed(check: ReconcileInput) -> Reconciliation:
    mix_id = check.operation.context.get("mix_id")
    if _key_of(check.operation, ATTACH) is None or check.keyring is None:
        return INCONCLUSIVE
    if not isinstance(mix_id, str) or MIX_ID.fullmatch(mix_id) is None:
        return INCONCLUSIVE
    try:
        opened = await open_connection(check.factory, check.keyring, check.ctx, "mowl")
        answer = await call_opened(
            check.factory, check.ctx, opened, lambda creds: mowl_api.swedebeat_mixes(creds)
        )
    except (ConnectionFailure, OutboundBlocked):
        return INCONCLUSIVE
    found = [m for m in parse_mixes(answer) if m.mix_id == mix_id and m.playlist_id is not None]
    if len(found) == 1:
        return Reconciliation("succeeded", str(found[0].playlist_id))
    return INCONCLUSIVE


KINDS: Final = (
    OPERATION_KINDS.register(OperationKind(CREATE, _never, _created)),
    OPERATION_KINDS.register(OperationKind(PUBLISH, _publish_listed, _published)),
    OPERATION_KINDS.register(OperationKind(MERGE, _never, _merged)),
    OPERATION_KINDS.register(OperationKind(ATTACH, _attach_listed, _attached)),
)
