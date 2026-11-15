"""One publication through the external-operation protocol (ADR 0034 section 5; ADR 0005
section 7), shared by the send and the removal.

The **decide step** (``perform``'s first fenced transaction) runs the guard first: the player
connection read at ``prepare`` share-locked while it is that generation and ``connected``
(``hold_player_link``; otherwise ``not_connected``), then the kind's own number check under the
publication row's lock (``Publication.take``; a number that is not this attempt's is
``internal``: nothing is sent).

The **call** maps the player's answer (``player_publish_policy.answer``) onto the protocol:

- 200 applied or repeat: ``succeeded``;
- 409 stale with ``last < N < 2^62``: ``failed`` with ``player_ahead``, and after the protocol
  returns the counter moves to N (a fenced update that never lowers it); any other N:
  ``internal``;
- 409 conflict: ``failed`` with ``player_conflict``; never resent with a new number;
- 401: ``failed`` with ``not_connected``, and the connection becomes ``needs_reconnect``;
- another 4xx: ``failed`` with ``invalid_input`` (not retried);
- never left: ``failed`` and retried with the same number and key (``ConnectionFailure``);
- anything else: ``uncertain``, one reconciliation (``classbeat.player_send.operation``).
"""

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from classbeat.connections.player_account import (
    PlayerLink,
    hold_player_link,
    mark_player_link_ended,
)
from classbeat.jobs import JobFailure, JobRun
from classbeat.operations import CallResult, Done, NotWritten, OperationRequest, UncertainAnswer
from classbeat.operations import perform as perform_operation
from classbeat.player_publish_policy import (
    answer,
    envelope,
    operation_key,
    publication_hash,
)
from classbeat.player_send import publications, source_calls
from classbeat.player_send.operation import REQUEST_SECONDS
from classbeat.player_send.runtime import Runtime

Take = Callable[[AsyncSession], Awaitable[bool]]
_FAILED: dict[str, str] = {
    "internal": "internal",
    "conflict": "player_conflict",
    "unauthorized": "not_connected",
    "refused": "invalid_input",
}


@dataclass(frozen=True, slots=True)
class Publication:
    """What one attempt publishes: a send (``document`` and its hash) or a removal (neither)."""

    kind: str
    source_class_id: UUID
    number: int
    document: Mapping[str, Any] | None
    document_hash: str | None
    class_id: UUID | None  # the operation row's class: the send's, never the removal's
    context: Mapping[str, Any]
    take: Take


@dataclass
class _Seen:
    ahead_to: int | None = None
    unauthorized: bool = False


async def publish(run: JobRun, runtime: Runtime, link: PlayerLink, pub: Publication) -> Done:
    """Raises ``JobFailure`` (its category says why) like ``perform``."""
    token = link.token
    if token is None:
        raise JobFailure("not_connected")
    seen = _Seen()
    owner_id = run.lease.owner_id

    async def guard(db: AsyncSession) -> bool:
        if not await hold_player_link(db, owner_id, link):
            return False
        if not await pub.take(db):
            raise JobFailure("internal")  # not this attempt's number: never sent
        return True

    body = envelope(pub.source_class_id, pub.number, pub.document, pub.document_hash)

    async def call() -> CallResult:
        found = await source_calls.publish(runtime.client_factory, token, body)
        judged = answer(found.status, found.body, pub.number, pub.number)
        if judged.answer == "applied":
            return CallResult(str(pub.number), {"publication_number": pub.number})
        if judged.answer == "ahead":
            seen.ahead_to = judged.ahead_to
            raise NotWritten("player_ahead")
        if judged.answer == "unauthorized":
            seen.unauthorized = True
        category = _FAILED.get(judged.answer)
        if category is None:
            raise UncertainAnswer
        raise NotWritten(category)

    request = OperationRequest(
        kind=pub.kind,
        idempotency_key=operation_key(pub.source_class_id, pub.number),
        request_hash=publication_hash(pub.kind, pub.source_class_id, pub.number, pub.document_hash),
        timeout_seconds=REQUEST_SECONDS,
        guard=guard,
        class_id=pub.class_id,
        context=pub.context,
    )
    try:
        return await perform_operation(run, request, call, keyring=runtime.keyring)
    except JobFailure:
        if seen.ahead_to is not None:
            async with run.fenced() as fs:
                await publications.move_ahead(fs.db, owner_id, pub.source_class_id, seen.ahead_to)
        if seen.unauthorized:
            await mark_player_link_ended(run.factory, run.ctx, link)
        raise
