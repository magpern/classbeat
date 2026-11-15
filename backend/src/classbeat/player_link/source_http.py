"""classbeat's calls to the player's source API for the link (ADR 0034 section 3), web process:

- ``POST /api/source/token`` ``{source, code, redirect_uri, code_verifier}``: the ``cbp_lc_``
  code for the source token and ``account_subject``;
- ``DELETE /api/source/link`` with ``Authorization: Bearer cbp_src_...``: the source ends its own
  link (a mismatch, a reconnect's old link, a disconnect).

The HTTP client comes from ``client_factory``. In production that is the player's scoped client
of ``classbeat.egress`` (``player_async_client``, ADR 0034 section 2: the one host, no
redirects, a total deadline, the size cap); tests hand a fake player's. This module adds its own
cap on what it reads (``BODY_MAX``) and follows nothing: a 3xx is a failure like any other
unexpected status.

Nothing here logs or raises a code, a verifier, a token or a body: only a category and the HTTP
status. A transport error, a deadline or a refusal by the allowlist are all ``unavailable``.
"""

import json
import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Final, Literal

import httpx

from classbeat import egress
from classbeat.player_link.link_policy import (
    INVALID_RESPONSE,
    MISMATCH,
    RATE_LIMITED,
    SOURCE_ID,
    UNAVAILABLE,
)

logger = logging.getLogger(__name__)

TOKEN_PATH: Final = "/api/source/token"  # noqa: S105 (a path)
LINK_PATH: Final = "/api/source/link"
BODY_MAX: Final = 64 * 1024
ClientFactory = Callable[[], httpx.AsyncClient]
Ended = Literal["ended", "gone", "failed"]


class _Unavailable(Exception):
    """No answer (never leaves this module)."""


@dataclass(frozen=True, slots=True, eq=False)
class Exchange:
    """``category`` when the exchange failed, else the parsed ``answer`` (never printed)."""

    category: str | None
    answer: dict[str, Any] | None = None

    def __repr__(self) -> str:
        return f"Exchange(category={self.category!r})"

    __str__ = __repr__


class PlayerSourceHttp:
    """The link's two calls to one player, at ``base_url`` (``https://<host>``)."""

    def __init__(self, base_url: str, client_factory: ClientFactory) -> None:
        self._base = base_url.rstrip("/")
        self._client_factory = client_factory

    @property
    def base_url(self) -> str:
        return self._base

    def __repr__(self) -> str:
        return f"PlayerSourceHttp(base_url={self._base!r})"

    async def _send(
        self, method: str, path: str, *, body: dict[str, str] | None, bearer: str | None
    ) -> tuple[int, bytes]:
        headers = {"Accept": "application/json"}
        if bearer is not None:
            headers["Authorization"] = f"Bearer {bearer}"
        content = None
        if body is not None:
            headers["Content-Type"] = "application/json"
            content = json.dumps(body).encode("utf-8")
        failed = False
        status, raw = 0, b""
        try:
            async with self._client_factory() as client:
                request = client.build_request(
                    method, f"{self._base}{path}", content=content, headers=headers
                )
                response = await client.send(request, stream=True, follow_redirects=False)
                try:
                    status = response.status_code
                    chunks: list[bytes] = []
                    size = 0
                    async for (
                        chunk
                    ) in response.aiter_bytes():  # decoded: the cap counts what is read
                        chunks.append(chunk)
                        size += len(chunk)
                        if size > BODY_MAX:
                            break
                    raw = b"".join(chunks)
                finally:
                    await response.aclose()
        except (egress.OutboundBlocked, httpx.HTTPError, TimeoutError, OSError):
            failed = True
        if failed:
            logger.warning("player request failed", extra={"category": UNAVAILABLE})
            raise _Unavailable
        return status, raw

    async def exchange(self, *, code: str, redirect_uri: str, verifier: str) -> Exchange:
        try:
            status, raw = await self._send(
                "POST",
                TOKEN_PATH,
                body={
                    "source": SOURCE_ID,
                    "code": code,
                    "redirect_uri": redirect_uri,
                    "code_verifier": verifier,
                },
                bearer=None,
            )
        except _Unavailable:
            return Exchange(UNAVAILABLE)
        category: str | None = None
        answer: dict[str, Any] | None = None
        if status == 200:
            answer = _object(raw)
            category = INVALID_RESPONSE if answer is None else None
        elif status == 429:
            category = RATE_LIMITED
        elif status in (400, 401, 403):
            category = MISMATCH  # invalid_grant: unknown, used, expired or a wrong binding
        elif status >= 500:
            category = UNAVAILABLE
        else:
            category = INVALID_RESPONSE
        if category is not None:
            logger.warning(
                "player token exchange refused", extra={"category": category, "http_status": status}
            )
        return Exchange(category, answer)

    async def end_link(self, token: str) -> Ended:
        """``ended`` (2xx), ``gone`` (401: the link was already ended) or ``failed`` (no answer,
        a 5xx or anything else: the link may still be listed on the player's site)."""
        try:
            status, _ = await self._send("DELETE", LINK_PATH, body=None, bearer=token)
        except _Unavailable:
            return "failed"
        if 200 <= status < 300:
            return "ended"
        if status == 401:
            return "gone"
        logger.warning("player link not ended", extra={"http_status": status})
        return "failed"


def _object(raw: bytes) -> dict[str, Any] | None:
    if len(raw) > BODY_MAX:
        return None
    try:
        data = json.loads(raw)
    except ValueError:
        return None
    return data if isinstance(data, dict) else None  # pyright: ignore[reportUnknownVariableType]
