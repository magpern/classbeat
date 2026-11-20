"""The spotdown client builder (ADR 0040 section 10, SD-P4, #1301): the one place that builds a
client that reaches spotdown, in the worker only. With ``classbeat.egress`` and
``classbeat.egress_gateway`` it is one of the modules that may build an HTTP client
(``tests/egress_scan.py`` scans it as allowed and refuses its test builder elsewhere).

**One exact origin, a setting.** ``CLASSBEAT_SPOTDOWN_URL``, checked by
``classbeat.spotdown.url_policy`` (an internal origin only: a single-label compose service name or
a loopback or private IP literal, ``http`` or ``https``, an explicit port), is installed once at the
worker's start with the service key by ``install_spotdown`` (the guard allows no other caller).
Unset, or without the key, nothing is installed and every builder here refuses
(``OutboundBlocked``) before a client exists: **with the setting empty nothing calls spotdown**.
The origin is on **no urllib list**, so the provider library's opener never reaches it. This
amends ``classbeat.egress``'s rule that "the list is code, not a setting", as the player's host
(ADR 0034 section 2) and the gateway's origin (ADR 0035 section 1) did.

**The service key never leaves this module's transport.** ``install_spotdown`` keeps it in a
``ServiceKey`` (constant ``repr``); the transport checks each request against the origin first and
only then sets ``Authorization: Bearer <key>``, replacing any value a caller set. So a call site
never holds the key, and the key can reach no other origin, scheme or port. It is never in a URL.

**The client** (one per call, ``async with``):

- **no redirect at all**: ``follow_redirects=False``, and any ``3xx`` answer is refused after the
  request (``OutboundBlocked`` with ``after_request``); spotdown never redirects;
- ``trust_env=False`` (no proxy or ``NETRC`` from the environment); TLS, for an ``https`` origin,
  from ``tls_client_context()`` (no key log);
- **no default header**: httpx's ``User-Agent``, ``Accept`` and ``Accept-Encoding`` are cleared;
- a **total deadline** per request, its body included (``RequestDeadline``), clamped to
  ``TOTAL_MIN_SECONDS`` to ``TOTAL_MAX_SECONDS``;
- **size caps**: an answer over ``ANSWER_MAX`` (a declared length or the streamed count) is
  refused (``AnswerTooLarge``); a request body over ``REQUEST_MAX`` is refused when its declared
  length is over it and cut off while it streams (``BodyTooLarge``). The callers cap each answer
  lower (``classbeat.spotdown.client``).
- **the one exception**: ``spotdown_upload_client()`` takes no argument and has
  ``UPLOAD_SECONDS`` and ``UPLOAD_MAX`` for one streamed ``PUT /v2/uploads``.

A transport and an origin of the test's own come only from tests, through
``spotdown_test_client``, which the guard refuses inside ``classbeat``.
"""

import asyncio
import math
import time
from collections.abc import AsyncIterator
from typing import Final

import httpx
from pydantic import SecretStr

from classbeat.egress import AnswerTooLarge, blocked, tls_client_context
from classbeat.egress_deadline import DeadlineStream, RequestDeadline
from classbeat.spotdown.key_policy import ServiceKey
from classbeat.spotdown.url_policy import SpotdownOrigin, spotdown_origin

TOTAL_MIN_SECONDS: Final = 1.0
TOTAL_MAX_SECONDS: Final = 120.0
CALL_SECONDS: Final = 15.0  # a job poll, a lookup, a scope delete
ANALYSIS_SECONDS: Final = 30.0  # start an analysis, read one
MATCH_SECONDS: Final = 60.0  # a batch of up to 50 tracks, metadata only
UPLOAD_SECONDS: Final = 300.0  # one streamed upload, its answer included
ANSWER_MAX: Final = 8 * 1024 * 1024  # the largest answer, an analysis document
REQUEST_MAX: Final = 1024 * 1024  # a JSON request body
UPLOAD_MAX: Final = 250 * 1024 * 1024  # as the mix fetch's cap (``design.mixfetch.MAX_BYTES``)
_DEFAULT_PORTS: Final = {"http": 80, "https": 443}

_installed: tuple[SpotdownOrigin, ServiceKey] | None = None


class BodyTooLarge(Exception):
    """A request body passed its cap while streaming; the rest was not sent. No text."""

    def __init__(self) -> None:
        super().__init__("request body too large")


def install_spotdown(url: str | None, key: SecretStr | None) -> None:
    """The origin (checked again here, ``SpotdownUrlRefused`` on a bad value) and the service key,
    or nothing: with either missing or empty spotdown is off and every builder refuses."""
    global _installed
    if not url or key is None or not key.get_secret_value():
        _installed = None
        return
    _installed = (spotdown_origin(url), ServiceKey(key))


def spotdown_installed() -> bool:
    """spotdown is configured in this process (an origin and a key are installed)."""
    return _installed is not None


def clamp_seconds(total_seconds: float) -> float:
    """``total_seconds`` within the bounds; a value that is not a finite number is refused."""
    if isinstance(total_seconds, bool) or not isinstance(total_seconds, int | float):
        raise ValueError("a spotdown deadline is a number of seconds")
    if not math.isfinite(total_seconds):
        raise ValueError("a spotdown deadline is finite")
    return min(max(float(total_seconds), TOTAL_MIN_SECONDS), TOTAL_MAX_SECONDS)


def _host(url: httpx.URL) -> str:
    return url.raw_host.decode("ascii", errors="replace").lower()


def same_origin(origin: SpotdownOrigin, url: httpx.URL) -> bool:
    """``url`` is exactly ``origin`` (scheme, host, port; a default port counts as written), with
    no userinfo."""
    scheme = url.scheme.lower()
    port = url.port if url.port is not None else _DEFAULT_PORTS.get(scheme)
    return (
        scheme == origin.scheme
        and _host(url) == origin.host
        and port == origin.port
        and not url.userinfo
    )


class _CappedBody(httpx.AsyncByteStream):
    """A request body that stops with ``BodyTooLarge`` once it passes ``cap`` bytes."""

    def __init__(self, inner: httpx.AsyncByteStream, cap: int) -> None:
        self._inner, self._cap = inner, cap

    async def __aiter__(self) -> AsyncIterator[bytes]:
        sent = 0
        async for chunk in self._inner:
            sent += len(chunk)
            if sent > self._cap:
                raise BodyTooLarge
            yield chunk

    async def aclose(self) -> None:
        await self._inner.aclose()


class _CappedAnswer(httpx.AsyncByteStream):
    def __init__(self, inner: httpx.AsyncByteStream) -> None:
        self._inner = inner

    async def __aiter__(self) -> AsyncIterator[bytes]:
        seen = 0
        async for chunk in self._inner:
            seen += len(chunk)
            if seen > ANSWER_MAX:
                raise AnswerTooLarge
            yield chunk

    async def aclose(self) -> None:
        await self._inner.aclose()


class _SpotdownTransport(httpx.AsyncBaseTransport):
    """Checks every request against the one origin before anything else, then sets the service
    key, caps the request body, refuses every ``3xx``, caps the answer and ends each request,
    its body included, by its deadline."""

    def __init__(
        self,
        installed: tuple[SpotdownOrigin, ServiceKey],
        inner: httpx.AsyncBaseTransport,
        total_seconds: float,
        body_cap: int,
    ) -> None:
        self._origin, self._key = installed
        self._inner = inner
        self._total_seconds = total_seconds
        self._body_cap = body_cap

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        at = time.monotonic() + self._total_seconds
        try:
            async with asyncio.timeout(at - time.monotonic()):
                response = await self._handle(request)
        except TimeoutError:
            raise RequestDeadline from None
        stream = response.stream
        if not isinstance(stream, httpx.AsyncByteStream):  # pragma: no cover - async transports
            await response.aclose()
            raise TypeError("an async transport answers with an async stream")
        return httpx.Response(
            response.status_code,
            headers=response.headers,
            stream=DeadlineStream(_CappedAnswer(stream), at),
            extensions=response.extensions,
            request=request,
        )

    async def _handle(self, request: httpx.Request) -> httpx.Response:
        if not same_origin(self._origin, request.url):
            raise blocked(_host(request.url))  # before the key is set: it never leaves for it
        length = request.headers.get("content-length")
        if length is not None and (not length.isdigit() or int(length) > self._body_cap):
            raise BodyTooLarge  # a declared body over the cap: nothing is sent
        request.headers["Authorization"] = self._key.bearer()
        body = request.stream
        if isinstance(body, httpx.AsyncByteStream):
            request.stream = _CappedBody(body, self._body_cap)
        response = await self._inner.handle_async_request(request)
        if 300 <= response.status_code < 400:
            await response.aclose()
            raise blocked(_host(request.url), after_request=True)
        declared = response.headers.get("content-length")
        if declared is not None and (not declared.isdigit() or int(declared) > ANSWER_MAX):
            await response.aclose()
            raise AnswerTooLarge
        return response

    async def aclose(self) -> None:
        await self._inner.aclose()


def spotdown_network_transport() -> httpx.AsyncHTTPTransport:
    """The network transport: the system trust store (``https`` only; ``http`` opens no TLS), no
    TLS key log, no proxy from the environment."""
    return httpx.AsyncHTTPTransport(trust_env=False, verify=tls_client_context())


def _spotdown_client(
    installed: tuple[SpotdownOrigin, ServiceKey],
    inner: httpx.AsyncBaseTransport,
    total_seconds: float,
    *,
    upload: bool = False,
) -> httpx.AsyncClient:
    total = UPLOAD_SECONDS if upload else clamp_seconds(total_seconds)
    cap = UPLOAD_MAX if upload else REQUEST_MAX
    client = httpx.AsyncClient(
        base_url=installed[0].url,
        transport=_SpotdownTransport(installed, inner, total, cap),
        trust_env=False,
        timeout=total,
        follow_redirects=False,
    )
    client.headers.clear()
    return client


def spotdown_async_client(total_seconds: float) -> httpx.AsyncClient:
    """The one client that reaches spotdown (module docstring). Its only argument is the deadline
    (clamped); with nothing installed it refuses (``OutboundBlocked``) before anything is built."""
    if _installed is None:
        raise blocked(None)
    return _spotdown_client(_installed, spotdown_network_transport(), total_seconds)


def spotdown_upload_client() -> httpx.AsyncClient:
    """The same client for one streamed upload (``UPLOAD_SECONDS``, ``UPLOAD_MAX``): no argument,
    so no caller can widen another call's deadline or cap."""
    if _installed is None:
        raise blocked(None)
    return _spotdown_client(_installed, spotdown_network_transport(), UPLOAD_SECONDS, upload=True)


def spotdown_test_client(
    origin: str,
    key: SecretStr,
    transport: httpx.AsyncBaseTransport,
    *,
    total_seconds: float = CALL_SECONDS,
    upload: bool = False,
) -> httpx.AsyncClient:
    """**Tests only** (the guard refuses it in ``classbeat``): the same client for an origin and a
    key of the test's own (checked by the same rules) over a fake transport."""
    installed = (spotdown_origin(origin), ServiceKey(key))
    return _spotdown_client(installed, transport, total_seconds, upload=upload)
