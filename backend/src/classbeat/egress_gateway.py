"""The SwedeBeat gateway client (ADR 0035 section 1, #1077): the one client that reaches the
gateway, in web (login) and the worker (match). Split out of ``classbeat.egress`` (CLAUDE.md
rule 2), which with this module is the only place that builds an HTTP client
(``tests/egress_scan.py`` scans both as allowed and refuses the test builder elsewhere).

**One exact origin.** The list is the configured ``(scheme, host, port)`` and nothing else: the
setting ``CLASSBEAT_SWEDEBEAT_GATEWAY_URL``, checked by ``classbeat.swedebeat_gateway.url_policy``
(``http`` only to a loopback or private IP literal with a port, else ``https`` to a public DNS
name) and installed once at start by ``install_swedebeat_gateway_origin``, called by the web's
and the worker's entry points only (the guard allows no other caller). Until then, and when the
setting is unset, ``swedebeat_gateway_async_client`` refuses (``OutboundBlocked``). The
``classbeat.egress.Allowlist`` is https/443-only, so this module has its own origin check: a
request to any other scheme, host or port is refused before it is sent. The origin is on **no
urllib list** (``WEB_URLLIB_HOSTS``, ``WORKER_HOSTS``), so the library's opener never reaches it.

**The client** (``swedebeat_gateway_async_client(total_seconds)``, one per call, ``async with``):

- **no redirect at all**: ``follow_redirects=False``; a 3xx to another origin is refused after
  the request (``OutboundBlocked`` with ``after_request``), a same-origin 3xx comes back as a
  response for the caller to refuse (``classbeat.swedebeat_gateway.status_policy``);
- ``trust_env=False``: httpx reads no proxy and no ``NETRC`` from the environment. It does not
  stop OpenSSL itself from reading ``SSL_CERT_FILE``/``SSL_CERT_DIR`` when it loads the system
  trust store (``tls_client_context()``, no key log), which is how the e2e stack's throwaway CA
  bundle is trusted; for ``http`` no TLS is used at all;
- **no default header**: httpx's ``User-Agent``, ``Accept`` and ``Accept-Encoding`` are cleared,
  so a request carries only what the call site sets (``classbeat.swedebeat_gateway.client``);
- a **total deadline** per request, its body included (``RequestDeadline``), clamped to
  ``TOTAL_MIN_SECONDS`` to ``TOTAL_MAX_SECONDS``: login takes ``LOGIN_SECONDS`` (30), the match
  ``MATCH_SECONDS`` (150; the contract asks for at least 120), the track download
  ``DOWNLOAD_SECONDS`` (120 for the whole answer, its 60 MB body included: ADR 0036 section 8's
  own deadline for the download, which amends ADR 0035 section 1; the 150 s clamp stays for
  every call but one).
- **the one exception**: ``swedebeat_gateway_upload_client()`` takes no argument and has
  ``MERGE_SECONDS`` (900) for the whole of a merge-publish, its streamed MP3 body and the answer
  included (ADR 0036 section 8, amending ADR 0035 section 1's clamp for that call only; SR C7).

A transport and an origin of the test's own come only from tests, through
``swedebeat_gateway_test_client``, which the guard refuses inside ``classbeat``.
"""

import asyncio
import math
import time
from typing import Final

import httpx

from classbeat.egress import blocked, tls_client_context
from classbeat.egress_deadline import DeadlineStream, RequestDeadline
from classbeat.swedebeat_gateway.url_policy import GatewayOrigin, gateway_origin

TOTAL_MIN_SECONDS: Final = 1.0
TOTAL_MAX_SECONDS: Final = 150.0
LOGIN_SECONDS: Final = 30.0
SESSION_SECONDS: Final = 30.0
MATCH_SECONDS: Final = 150.0
DOWNLOAD_SECONDS: Final = 120.0  # ADR 0036 section 8: the track download, its body included
MERGE_SECONDS: Final = 900.0  # ADR 0036 section 8: merge-publish, its streamed upload included
_DEFAULT_PORTS: Final = {"http": 80, "https": 443}

_origin: GatewayOrigin | None = None


def install_swedebeat_gateway_origin(url: str | None) -> None:
    """The gateway's origin, checked again here (``GatewayUrlRefused`` on a bad value), or
    ``None``: SwedeBeat is unavailable and the client refuses."""
    global _origin
    _origin = None if url is None else gateway_origin(url)


def swedebeat_gateway_origin() -> GatewayOrigin | None:
    """The installed origin, or ``None``."""
    return _origin


def clamp_seconds(total_seconds: float) -> float:
    """``total_seconds`` within ``TOTAL_MIN_SECONDS`` and ``TOTAL_MAX_SECONDS``; a value that is
    not a finite number is refused (``ValueError``)."""
    if isinstance(total_seconds, bool) or not isinstance(total_seconds, int | float):
        raise ValueError("a gateway deadline is a number of seconds")
    if not math.isfinite(total_seconds):
        raise ValueError("a gateway deadline is finite")
    return min(max(float(total_seconds), TOTAL_MIN_SECONDS), TOTAL_MAX_SECONDS)


def _host(url: httpx.URL) -> str:
    return url.raw_host.decode("ascii", errors="replace").lower()


def same_origin(origin: GatewayOrigin, url: httpx.URL) -> bool:
    """``url`` is exactly ``origin``: the scheme, the host and the port (a default port counts
    as written), with no userinfo."""
    scheme = url.scheme.lower()
    port = url.port if url.port is not None else _DEFAULT_PORTS.get(scheme)
    return (
        scheme == origin.scheme
        and _host(url) == origin.host
        and port == origin.port
        and not url.userinfo
    )


class _GatewayTransport(httpx.AsyncBaseTransport):
    """Checks every request against the one origin before the inner transport sees it, refuses
    a redirect to another origin, and ends each request, its body included, by its deadline."""

    def __init__(
        self, origin: GatewayOrigin, inner: httpx.AsyncBaseTransport, total_seconds: float
    ) -> None:
        self._origin = origin
        self._inner = inner
        self._total_seconds = total_seconds

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
            stream=DeadlineStream(stream, at),
            extensions=response.extensions,
            request=request,
        )

    async def _handle(self, request: httpx.Request) -> httpx.Response:
        if not same_origin(self._origin, request.url):
            raise blocked(_host(request.url))
        response = await self._inner.handle_async_request(request)
        location = response.headers.get("location")
        if response.is_redirect and location is not None:
            target: httpx.URL | None
            try:
                target = request.url.join(location)
            except (httpx.InvalidURL, ValueError):
                target = None
            if target is None or not same_origin(self._origin, target):
                await response.aclose()
                raise blocked(None if target is None else _host(target), after_request=True)
        return response

    async def aclose(self) -> None:
        await self._inner.aclose()


def gateway_network_transport() -> httpx.AsyncHTTPTransport:
    """The network transport: the system trust store (used for ``https`` only; an ``http``
    origin opens no TLS), no TLS key log, no proxy from the environment."""
    return httpx.AsyncHTTPTransport(trust_env=False, verify=tls_client_context())


def _gateway_client(
    origin: GatewayOrigin,
    inner: httpx.AsyncBaseTransport,
    total_seconds: float,
    *,
    upload: bool = False,
) -> httpx.AsyncClient:
    total = MERGE_SECONDS if upload else clamp_seconds(total_seconds)
    client = httpx.AsyncClient(
        base_url=origin.url,
        transport=_GatewayTransport(origin, inner, total),
        trust_env=False,
        timeout=total,
        follow_redirects=False,
    )
    client.headers.clear()
    return client


def swedebeat_gateway_async_client(total_seconds: float) -> httpx.AsyncClient:
    """The one client that reaches the gateway (module docstring). Its only argument is the
    deadline (clamped); the origin is the installed one, and with none it refuses
    (``OutboundBlocked``) before anything is built."""
    if _origin is None:
        raise blocked(None)
    return _gateway_client(_origin, gateway_network_transport(), total_seconds)


def swedebeat_gateway_upload_client() -> httpx.AsyncClient:
    """The same client with ``MERGE_SECONDS`` for one merge-publish (module docstring): no
    argument, so no caller can widen another call's deadline."""
    if _origin is None:
        raise blocked(None)
    return _gateway_client(_origin, gateway_network_transport(), MERGE_SECONDS, upload=True)


def swedebeat_gateway_test_client(
    origin: str,
    transport: httpx.AsyncBaseTransport,
    *,
    total_seconds: float = LOGIN_SECONDS,
    upload: bool = False,
) -> httpx.AsyncClient:
    """**Tests only** (the guard refuses it in ``classbeat``): the same client for an origin of
    the test's own (checked by the same policy) over a fake transport. The origin check, the
    redirect, header and deadline behaviour are the production client's (the same builder);
    ``upload`` is the merge-publish's (``MERGE_SECONDS``, ``total_seconds`` ignored)."""
    return _gateway_client(gateway_origin(origin), transport, total_seconds, upload=upload)
