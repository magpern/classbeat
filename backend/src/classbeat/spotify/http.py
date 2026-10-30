"""classbeat's own Spotify HTTP client (ADR 0009 sections 4 to 6, 10): the token endpoint (code
exchange at connect, refresh) and the ``GET /me`` of a connect. The **only** module that calls
the token endpoint; the library never does (``spotify_refresh_enabled=False``).

- Every request goes through ``classbeat.egress.spotify_async_client`` (the Spotify allowlist,
  ``trust_env=False``, no redirects followed, a total deadline covering the body).
- **No Client Secret, ever**: the token requests are PKCE public-client requests with
  ``client_id`` in the form and no ``client_secret`` parameter or Basic ``Authorization``.
- Errors become ``ConnectionFailure(category)`` with no chained exception and no message: the
  token endpoint's JSON ``error`` code is read (a short enumerated value) and the body is parsed
  and discarded, never logged and never put in an exception. ``Retry-After`` is kept.
- Answers are validated: the access token and refresh token are non-empty strings of a bounded
  length, ``expires_in`` a positive integer, the Spotify user id 1 to 100 printable characters.
"""

import json
import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Final, NoReturn

import httpx

from classbeat import egress
from classbeat.connections import ConnectionFailure, RefreshedTokens
from classbeat.connections.errors import MAX_RETRY_AFTER
from classbeat.spotify.errors import by_oauth_code, by_status

logger = logging.getLogger(__name__)

ACCOUNTS: Final = f"https://{egress.SPOTIFY_ACCOUNTS_HOST}"
AUTHORIZE_URL: Final = f"{ACCOUNTS}/authorize"
TOKEN_URL: Final = f"{ACCOUNTS}/api/token"
ME_URL: Final = f"https://{egress.SPOTIFY_API_HOST}/v1/me"
FORM: Final = {"Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json"}
BODY_MAX: Final = 64 * 1024  # bytes read from an answer; more is an invalid answer
TOKEN_MAX: Final = 2048
EXPIRES_MAX: Final = 86_400
USER_ID_MAX: Final = 100
DEFAULT_TIMEOUT_SECONDS: Final = egress.SPOTIFY_TOTAL_SECONDS
_RETRY_DIGITS: Final = 6

# total_seconds -> the allowlisted client. Tests hand the fake Spotify's (``tests/spotifyfake``).
ClientFactory = Callable[[float], httpx.AsyncClient]


def _network_client(total_seconds: float) -> httpx.AsyncClient:
    return egress.spotify_async_client(total_seconds=total_seconds)


@dataclass(frozen=True, slots=True, eq=False)
class GrantedTokens:
    """The answer to a code exchange. Never printed."""

    access_token: str
    refresh_token: str | None
    expires_in: int
    scope: str

    def __repr__(self) -> str:
        return "GrantedTokens(...)"

    __str__ = __repr__

    def __reduce__(self) -> NoReturn:
        raise TypeError("tokens are never serialized")


class _Invalid(Exception):
    """An answer that is not what Spotify answers (never leaves this module)."""


def _retry_after(response: httpx.Response) -> int | None:
    value = response.headers.get("retry-after", "").strip()
    if not value or len(value) > _RETRY_DIGITS or not value.isdigit():
        return None
    return min(int(value), MAX_RETRY_AFTER)


def _token(value: object) -> str:
    if not isinstance(value, str) or not 1 <= len(value) <= TOKEN_MAX or not value.isprintable():
        raise _Invalid
    return value


def _expires_in(value: object) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or not 1 <= value <= EXPIRES_MAX:
        raise _Invalid
    return value


def _object(body: bytes) -> dict[str, Any]:
    if len(body) > BODY_MAX:
        raise _Invalid
    data = json.loads(body)
    if not isinstance(data, dict):
        raise _Invalid
    return data  # pyright: ignore[reportUnknownVariableType]


class SpotifyHttp:
    """The token endpoint and ``GET /me``. One client per request (each request has its own
    total deadline of ``timeout_seconds``)."""

    def __init__(
        self,
        client_factory: ClientFactory = _network_client,
        *,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        if not timeout_seconds > 0:
            raise ValueError("a timeout is positive")
        self._client_factory = client_factory
        self._timeout = timeout_seconds

    async def _send(
        self,
        method: str,
        url: str,
        *,
        form: dict[str, str] | None = None,
        bearer: str | None = None,
    ) -> tuple[int, bytes, int | None]:
        """Status, body (at most ``BODY_MAX`` + 1 bytes) and ``Retry-After``. A transport
        failure or a deadline is ``upstream_unavailable``; nothing is chained."""
        failed = False
        status, body, hint = 0, b"", None
        headers = dict(FORM) if form is not None else {"Accept": "application/json"}
        if bearer is not None:
            headers["Authorization"] = f"Bearer {bearer}"
        try:
            async with self._client_factory(self._timeout) as client:
                request = client.build_request(method, url, data=form, headers=headers)
                response = await client.send(request, stream=True)
                try:
                    status, hint = response.status_code, _retry_after(response)
                    chunks: list[bytes] = []
                    size = 0
                    async for chunk in response.aiter_raw():
                        chunks.append(chunk)
                        size += len(chunk)
                        if size > BODY_MAX:
                            break
                    body = b"".join(chunks)
                finally:
                    await response.aclose()
        except egress.OutboundBlocked:
            raise  # the allowlist's refusal: not the connection's fault (the job fails on it)
        except (httpx.HTTPError, TimeoutError, OSError):
            failed = True
        if failed:
            logger.warning("spotify request failed", extra={"category": "upstream_unavailable"})
            raise ConnectionFailure("upstream_unavailable")
        return status, body, hint

    async def _token_request(self, form: dict[str, str]) -> dict[str, Any]:
        status, body, hint = await self._send("POST", TOKEN_URL, form=form)
        category: str | None = None
        data: dict[str, Any] | None = None
        try:
            parsed = _object(body)
        except (ValueError, _Invalid):
            parsed = None
        if status == 200 and parsed is not None:
            data = parsed
        elif status == 200:
            category = "invalid_response"
        else:
            code = parsed.get("error") if parsed is not None else None
            category = by_oauth_code(status, code, hint)
        if category is not None:
            logger.warning(
                "spotify token request refused",
                extra={"category": category, "http_status": status},
            )
            raise ConnectionFailure(
                category, hint if category in ("rate_limited", "quota_exhausted") else None,
                not_applied=True, http_status=status,
            )  # fmt: skip
        assert data is not None  # noqa: S101 - set above
        return data

    async def exchange_code(
        self, *, client_id: str, code: str, redirect_uri: str, verifier: str
    ) -> GrantedTokens:
        """``authorization_code`` with PKCE: the stored ``client_id``, ``redirect_uri`` and
        ``code_verifier``; no ``client_secret``."""
        data = await self._token_request(
            {
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": redirect_uri,
                "client_id": client_id,
                "code_verifier": verifier,
            }
        )
        tokens: GrantedTokens | None
        try:
            refresh = data.get("refresh_token")
            scope = data.get("scope")
            tokens = GrantedTokens(
                _token(data.get("access_token")),
                None if refresh is None else _token(refresh),
                _expires_in(data.get("expires_in")),
                scope if isinstance(scope, str) else "",
            )
        except _Invalid:
            tokens = None
        if tokens is None:
            raise ConnectionFailure("invalid_response", not_applied=True)
        return tokens

    async def refresh(self, client_id: str, refresh_token: str) -> RefreshedTokens:
        """``refresh_token``; Spotify may or may not rotate the refresh token."""
        data = await self._token_request(
            {"grant_type": "refresh_token", "refresh_token": refresh_token, "client_id": client_id}
        )
        tokens: RefreshedTokens | None
        try:
            refresh = data.get("refresh_token")
            tokens = RefreshedTokens(
                _token(data.get("access_token")),
                None if refresh is None else _token(refresh),
                _expires_in(data.get("expires_in")),
            )
        except _Invalid:
            tokens = None
        if tokens is None:
            raise ConnectionFailure("invalid_response", not_applied=True)
        return tokens

    async def me(self, access_token: str) -> str:
        """The Spotify user id of ``access_token`` (``GET /me``, at connect). A 403 is
        ``not_allowlisted``, a 401 ``invalid_grant``."""
        status, body, hint = await self._send("GET", ME_URL, bearer=access_token)
        user_id: object = None
        if status == 200:
            try:
                user_id = _object(body).get("id")
            except (ValueError, _Invalid):
                user_id = None
            if (
                isinstance(user_id, str)
                and 1 <= len(user_id) <= USER_ID_MAX
                and user_id.isprintable()
            ):
                return user_id
            category = "invalid_response"
        else:
            category = by_status(status, hint, connect=True)
        logger.warning(
            "spotify verification failed", extra={"category": category, "http_status": status}
        )
        raise ConnectionFailure(
            category, hint if category in ("rate_limited", "quota_exhausted") else None,
            not_applied=True, http_status=status,
        )  # fmt: skip
