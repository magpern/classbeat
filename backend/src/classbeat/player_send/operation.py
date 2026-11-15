"""The external operations ``player_send`` and ``player_remove`` (ADR 0034 section 5).

Both keep in their ``request_context`` the class's id and the publication number (a send also
the revision and the document hash, ``sha256:<hex>``): ids and hashes only, never the document or
the token.

- **Reconciler** (step 4 once, and the user's **Check again**): the player's numbers for the
  class (``source_calls.highest``), judged by ``player_publish_policy.reconciled``. Without the
  worker's runtime (the web process), without a readable token or without an answer it is
  inconclusive: the row stays ``uncertain``.
- **``on_succeeded``** of a send: the publication row records the revision and the document hash,
  unless a later number was taken meanwhile. A removal writes nothing more.
"""

import re
from collections.abc import Mapping
from typing import Any, Final
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from classbeat.connections.player_account import read_player_link
from classbeat.operations import (
    INCONCLUSIVE,
    OPERATION_KINDS,
    Operation,
    OperationKind,
    ReconcileInput,
    Reconciliation,
)
from classbeat.player_publish_policy import REMOVE_KIND, SEND_KIND, reconciled, valid_number
from classbeat.player_send import publications, source_calls
from classbeat.player_send.runtime import current_runtime

DOCUMENT_HASH: Final = re.compile(r"sha256:([0-9a-f]{64})")
REQUEST_SECONDS: Final = 15.0  # longer than the client's total deadline (10 s)


def send_context(
    source_class_id: UUID, number: int, revision_id: UUID, document_hash: str
) -> dict[str, Any]:
    return {
        "source_class_id": str(source_class_id),
        "publication_number": number,
        "revision_id": str(revision_id),
        "document_hash": document_hash,
    }


def remove_context(source_class_id: UUID, number: int) -> dict[str, Any]:
    return {"source_class_id": str(source_class_id), "publication_number": number}


def _uuid(value: object) -> UUID | None:
    if not isinstance(value, str):
        return None
    try:
        return UUID(value)
    except ValueError:
        return None


def _target(context: Mapping[str, Any]) -> tuple[UUID, int] | None:
    class_id = _uuid(context.get("source_class_id"))
    number = context.get("publication_number")
    if class_id is None or not valid_number(number) or not isinstance(number, int):
        return None
    return class_id, number


async def _reconcile(check: ReconcileInput) -> Reconciliation:
    runtime = current_runtime()
    target = _target(check.operation.context)
    if runtime is None or target is None:
        return INCONCLUSIVE
    class_id, number = target
    link = await read_player_link(check.factory, runtime.keyring, check.ctx)
    if link is None or link.token is None:
        return INCONCLUSIVE
    found = await source_calls.highest(runtime.client_factory, link.token, class_id)
    if found is None:
        return INCONCLUSIVE
    removed = check.operation.kind == REMOVE_KIND
    outcome = reconciled(found.status, found.body, class_id, number, removed)
    if outcome == "succeeded":
        return Reconciliation("succeeded", str(number))
    return Reconciliation(outcome)


async def _sent(db: AsyncSession, owner_id: UUID, operation: Operation) -> None:
    target = _target(operation.context)
    revision_id = _uuid(operation.context.get("revision_id"))
    digest = DOCUMENT_HASH.fullmatch(str(operation.context.get("document_hash", "")))
    if target is None or revision_id is None or digest is None:
        return
    class_id, number = target
    await publications.record_sent(
        db, owner_id, class_id, number, revision_id, bytes.fromhex(digest.group(1))
    )


SEND: Final = OPERATION_KINDS.register(OperationKind(SEND_KIND, _reconcile, _sent))
REMOVE: Final = OPERATION_KINDS.register(OperationKind(REMOVE_KIND, _reconcile))
