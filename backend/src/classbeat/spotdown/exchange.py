"""One request to spotdown, its answer read under a cap, every exception turned into a reason
(``classbeat.spotdown.client``'s helper). The client comes from the caller's opener (the egress
builder), and is opened and closed here, ``async with``, so nothing outlives the call.

The body of a success is read raw with identity encoding only, under the caller's cap; an error's
under ``ERROR_ANSWER_MAX`` (its envelope's ``code`` only). ``OutboundBlocked`` is ``blocked``,
``AnswerTooLarge`` and ``BodyTooLarge`` are ``too_large``, a connection error, a timeout or the
total deadline is ``connection``. No exception is kept, chained or logged: httpx's errors hold the
request, whose headers hold the service key.
"""

from collections.abc import AsyncIterable, Callable
from dataclasses import dataclass, field
from typing import Final

import httpx

from classbeat.connections.errors import retry_after_of
from classbeat.egress import AnswerTooLarge, OutboundBlocked
from classbeat.egress_spotdown import BodyTooLarge
from classbeat.spotdown import request_model as rq
from classbeat.spotdown.answer_model import parse_error_code
from classbeat.spotdown.failure_policy import TrackFailure, Unreachable, http_outcome
from classbeat.spotdown.scope_calc import IDEMPOTENCY_PATTERN
from classbeat.swedebeat_gateway.answer import read_capped

ERROR_ANSWER_MAX: Final = 16 * 1024
JSON: Final = "application/json"

Failure = TrackFailure | Unreachable


@dataclass(frozen=True, slots=True)
class Answer:
    status: int
    body: bytes | None = field(repr=False)
    retry_after: int | None = None


def headers_for(*, body: str | None, idempotency_key: str | None) -> dict[str, str]:
    headers = {"Accept": JSON, "Accept-Encoding": "identity"}
    if body is not None:
        headers["Content-Type"] = body
    if idempotency_key is not None:
        if not IDEMPOTENCY_PATTERN.fullmatch(idempotency_key):
            raise rq.RequestRefused("an Idempotency-Key is scope_calc.idempotency_key's")
        headers["Idempotency-Key"] = idempotency_key
    return headers


async def exchange(
    open_client: Callable[[], httpx.AsyncClient],
    method: str,
    path: str,
    *,
    success: frozenset[int],
    cap: int,
    headers: dict[str, str],
    params: dict[str, str] | None = None,
    content: bytes | AsyncIterable[bytes] | None = None,
) -> Answer | Unreachable:
    """One request: the status and the capped body of a success or an error envelope, or why
    there is none. Every exception is turned into a reason here; none is kept or chained."""
    reason: Unreachable | None = None
    answer: Answer | None = None
    try:
        async with (
            open_client() as client,
            client.stream(method, path, headers=headers, params=params, content=content) as reply,
        ):
            status = reply.status_code
            limit = cap if status in success else ERROR_ANSWER_MAX
            body = await read_capped(reply, limit) if status != 204 else b""
            answer = Answer(status, body, retry_after_of(reply))
    except OutboundBlocked:
        reason = Unreachable("blocked")
    except (AnswerTooLarge, BodyTooLarge):
        reason = Unreachable("too_large")
    except (httpx.HTTPError, TimeoutError, OSError):
        reason = Unreachable("connection")
    if reason is not None or answer is None:
        return reason or Unreachable("connection")
    if answer.status in success and answer.body is None:
        return Unreachable("too_large")  # over its cap, or an encoded body
    return answer


def failure(answer: Answer) -> Failure:
    return http_outcome(answer.status, parse_error_code(answer.body), answer.retry_after)
