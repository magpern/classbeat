"""The listening preview's Spotify requests (ADR 0042 section 7.2; ADR 0009 section 10 as
amended): the four ``player_policy.PLAYER_CALLS`` and nothing else, web process only.

- **The client** is the existing Spotify async client, ``egress.spotify_async_client`` (the
  Spotify allowlist, ``trust_env=False``, no redirects followed, a total deadline covering the
  body), one per request. No new client construction.
- **The path guard**: every request is built here from a ``PlayerCall`` and checked with
  ``player_policy.allowed`` (method, https, ``api.spotify.com``, the exact path, only its query
  keys) right before it is sent; anything else raises ``egress.OutboundBlocked`` and is never
  sent.
- **The token** is the ``Authorization: Bearer`` header of the one request and nothing else: it
  is never logged, never in an exception and never in ``PlayerAnswer``.
- **The answer**: its status, its JSON object (``None`` for an empty or non-JSON body; at most
  ``BODY_MAX`` decoded bytes, ``CappedBody``), ``Retry-After`` and the request's own round trip
  in milliseconds. A transport failure or the deadline is ``ConnectionFailure
  ("upstream_unavailable")`` with nothing chained; the body of an error answer is discarded.
"""

import json
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Final

import httpx

from classbeat import egress
from classbeat.connections import ConnectionFailure
from classbeat.connections.errors import retry_after_of
from classbeat.spotify.answer_body import CappedBody
from classbeat.spotify.player_policy import PlayerCall, allowed

logger = logging.getLogger(__name__)

BODY_MAX: Final = 256 * 1024  # a state answer's item carries markets, artists and an album
TOTAL_SECONDS: Final = 5.0  # each player request, its body included
ACCEPT: Final = {"Accept": "application/json", "Accept-Encoding": "identity"}

ClientFactory = Callable[[float], httpx.AsyncClient]


def _network_client(total_seconds: float) -> httpx.AsyncClient:
    return egress.spotify_async_client(total_seconds=total_seconds)


@dataclass(frozen=True, slots=True)
class PlayerAnswer:
    status: int
    data: dict[str, Any] | None
    retry_after: int | None
    round_trip_ms: int


def _object(body: bytes) -> dict[str, Any] | None:
    if not body or len(body) > BODY_MAX:
        return None
    try:
        data = json.loads(body)
    except ValueError:
        return None
    return data if isinstance(data, dict) else None  # pyright: ignore[reportUnknownVariableType]


class SpotifyPlayerHttp:
    """Sends one ``PlayerCall`` with a user's access token."""

    def __init__(
        self,
        client_factory: ClientFactory = _network_client,
        *,
        timeout_seconds: float = TOTAL_SECONDS,
    ) -> None:
        if not timeout_seconds > 0:
            raise ValueError("a timeout is positive")
        self._client_factory = client_factory
        self._timeout = timeout_seconds

    async def send(
        self,
        call: PlayerCall,
        bearer: str,
        *,
        device_id: str | None = None,
        body: dict[str, Any] | None = None,
    ) -> PlayerAnswer:
        url = f"https://{egress.SPOTIFY_API_HOST}{call.path}"
        params = {"device_id": device_id} if device_id is not None else None
        headers = {**ACCEPT, "Authorization": f"Bearer {bearer}"}
        failed = False
        status, raw, hint, started = 0, b"", None, time.monotonic()
        try:
            async with self._client_factory(self._timeout) as client:
                request = client.build_request(
                    call.method, url, params=params, json=body, headers=headers
                )
                keys = frozenset(k for k, _ in request.url.params.multi_items())
                if not allowed(
                    request.method, request.url.scheme, request.url.host, request.url.path, keys
                ):
                    raise egress.OutboundBlocked(request.url.host)
                started = time.monotonic()
                response = await client.send(request, stream=True)
                try:
                    status, hint = response.status_code, retry_after_of(response)
                    capped = CappedBody(response.headers.get("content-encoding"), BODY_MAX)
                    async for chunk in response.aiter_raw():
                        if not capped.feed(chunk):
                            break
                    raw = capped.body() or b""
                finally:
                    await response.aclose()
        except egress.OutboundBlocked:
            raise
        except (httpx.HTTPError, TimeoutError, OSError):
            failed = True
        if failed:
            logger.warning(
                "spotify player request failed", extra={"category": "upstream_unavailable"}
            )
            raise ConnectionFailure("upstream_unavailable")
        round_trip = max(0, round((time.monotonic() - started) * 1000))
        data = _object(raw) if 200 <= status < 300 else None
        return PlayerAnswer(status, data, hint, round_trip)
