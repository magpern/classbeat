"""classbeat's two calls to the player's source API for a publication (ADR 0034 section 5):

- ``POST /api/source/publications`` with the envelope (``player_publish_policy.envelope``);
- ``GET /api/source/classes/{source_class_id}``: the player's numbers only, for the one
  reconciliation and **Check again**.

Both send ``Authorization: Bearer <source token>``. The client comes from the runtime's factory:
in the worker the scoped client of ``classbeat.egress`` (``player_async_client``: the one host, no
redirects, a total deadline, the answer cap); tests hand a fake player's.

``publish`` raises ``ConnectionFailure(not_applied=True)`` only when the request provably never
left: no client could be made (the player's host is not installed), or the connection was never
opened (``httpx.ConnectError``, ``httpx.ConnectTimeout``: a DNS failure, a refusal). Anything
else after the request was built (a timeout while waiting, a lost connection, an answer over the
cap, a refused redirect) is ``UncertainAnswer``: it may have arrived. Nothing here logs or raises
a token or a body, only a category or a status.
"""

import json
import logging
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, cast
from uuid import UUID

import httpx

from classbeat import egress
from classbeat.connections import ConnectionFailure
from classbeat.operations import UncertainAnswer
from classbeat.player_publish_policy import CLASSES_PATH, PUBLICATIONS_PATH

logger = logging.getLogger(__name__)

ClientFactory = Callable[[], httpx.AsyncClient]
BODY_MAX = 64 * 1024


@dataclass(frozen=True, slots=True, eq=False)
class Answer:
    """The player's status and its JSON object (None when the body is not one)."""

    status: int
    body: Mapping[str, Any] | None

    def __repr__(self) -> str:
        return f"Answer(status={self.status})"


def _object(raw: bytes) -> Mapping[str, Any] | None:
    if len(raw) > BODY_MAX:
        return None
    try:
        data = json.loads(raw)
    except ValueError:
        return None
    return cast(dict[str, Any], data) if isinstance(data, dict) else None


def _client(factory: ClientFactory) -> httpx.AsyncClient:
    try:
        return factory()
    except egress.OutboundBlocked:
        # No client, so no request: the player is not configured in this process.
        raise ConnectionFailure("upstream_unavailable", not_applied=True) from None


async def _request(
    factory: ClientFactory, method: str, path: str, token: str, body: Mapping[str, Any] | None
) -> Answer:
    headers = {"Accept": "application/json", "Authorization": f"Bearer {token}"}
    content = None
    if body is not None:
        headers["Content-Type"] = "application/json"
        content = json.dumps(dict(body), ensure_ascii=False).encode("utf-8")
    client = _client(factory)
    async with client:
        request = client.build_request(method, path, content=content, headers=headers)
        response = await client.send(request, stream=True, follow_redirects=False)
        try:
            raw = await response.aread()
        finally:
            await response.aclose()
    return Answer(response.status_code, _object(raw))


async def publish(factory: ClientFactory, token: str, body: Mapping[str, Any]) -> Answer:
    """One publication; see the module docstring for what each failure means."""
    try:
        return await _request(factory, "POST", PUBLICATIONS_PATH, token, body)
    except (httpx.ConnectError, httpx.ConnectTimeout):
        logger.warning("player publication never left", extra={"category": "upstream_unavailable"})
        raise ConnectionFailure("upstream_unavailable", not_applied=True) from None
    except ConnectionFailure:
        raise
    except Exception as exc:
        logger.warning("player publication unanswered", extra={"error_type": type(exc).__name__})
        raise UncertainAnswer from None


async def highest(factory: ClientFactory, token: str, source_class_id: UUID) -> Answer | None:
    """The player's numbers for the class, or None when there was no answer."""
    try:
        return await _request(factory, "GET", f"{CLASSES_PATH}{source_class_id}", token, None)
    except Exception as exc:
        logger.warning("player numbers unanswered", extra={"error_type": type(exc).__name__})
        return None
