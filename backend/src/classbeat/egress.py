"""The worker's outbound allowlist (ADR 0006 §9; threat model T19, T49).

The only place in ``classbeat`` that builds an HTTP client or an ``urllib`` opener (the guard in
``tests/egress_scan.py`` fails on any other). Two paths leave the worker:

- **urllib**, used by the provider library (``mowl-workouts`` calls ``urllib.request.urlopen``).
  ``install_worker_allowlist()`` installs, before anything else in the worker, a process-wide
  opener with an empty ``ProxyHandler`` (no ``HTTPS_PROXY`` or ``NO_PROXY`` setting reroutes a
  call) and a guard that refuses every request, redirect hops included, that is not ``https`` to
  an allowlisted host on port 443. It has no ``http``, ``ftp``, ``file`` or ``data`` handler.
- **httpx**, for the model client (A2): ``allowlisted_async_client()`` returns an
  ``httpx.AsyncClient`` with ``trust_env=False`` whose transport applies the same check to every
  request, so every redirect hop too.

A refused request raises ``OutboundBlocked`` (the job fails ``outbound_blocked``, ADR 0005 §4)
and logs the host only, never the URL, its path or query, or a header.

The list is code, not a setting: nothing in the environment can widen it. This is enforced inside
the process; code running in the worker could still open a socket around it (accepted until the
network-level egress rule of M6, threat model).
"""

import logging
import ssl
import urllib.request
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

import httpx

logger = logging.getLogger(__name__)

HTTPS = "https"
HTTPS_PORT = 443
# M3a: the MOWL API (the library's base URL) and the model API. M4 adds the Spotify hosts.
WORKER_HOSTS = frozenset({"api.mowl.com", "api.anthropic.com"})
MODEL_TIMEOUT_SECONDS = 120.0  # ADR 0006 §2
_LOGGED_HOST_MAX = 253  # the longest DNS name


class OutboundBlocked(Exception):
    """A request the allowlist refused. ``args == ("outbound request blocked",)``; ``host`` is
    the refused host (lower case, printable, at most 253 characters), never the URL."""

    def __init__(self, host: str) -> None:
        super().__init__("outbound request blocked")
        self.host = host


def _printable_host(host: str | None) -> str:
    if not host:
        return ""
    text = "".join(c if c.isprintable() and not c.isspace() else "?" for c in host)
    return text.lower()[:_LOGGED_HOST_MAX]


@dataclass(frozen=True, slots=True)
class Allowlist:
    """``https`` to one of ``hosts`` (exact, lower case) on port 443, nothing else."""

    hosts: frozenset[str]

    def __post_init__(self) -> None:
        if any(h != h.lower() or not h or h.endswith(".") for h in self.hosts):
            raise ValueError("allowlisted hosts are lower case DNS names without a trailing dot")

    def check(self, scheme: str, host: str | None, port: int | None, userinfo: bool) -> str:
        """The host (lower case) if the request may go out; else log, raise ``OutboundBlocked``."""
        allowed = (
            scheme.lower() == HTTPS
            and host is not None
            and host.lower() in self.hosts
            and port in (None, HTTPS_PORT)
            and not userinfo
        )
        if not allowed:
            raise blocked(host)
        assert host is not None  # noqa: S101 - narrowed by ``allowed``
        return host.lower()

    def check_url(self, url: str) -> str:
        """``check`` for a URL string; one that does not parse is refused."""
        try:
            parts = urlsplit(url)
            port = parts.port
        except ValueError:
            raise blocked(None) from None
        has_userinfo = parts.username is not None or parts.password is not None
        return self.check(parts.scheme, parts.hostname, port, has_userinfo)


WORKER_ALLOWLIST = Allowlist(WORKER_HOSTS)


def blocked(host: str | None) -> OutboundBlocked:
    """Log the refusal (host only) and return the exception to raise."""
    shown = _printable_host(host)
    logger.warning("outbound request blocked", extra={"host": shown})
    return OutboundBlocked(shown)


# --- urllib --------------------------------------------------------------------------------


def _check_urllib_request(allowlist: Allowlist, req: urllib.request.Request) -> None:
    host = allowlist.check_url(req.full_url)
    # What http.client will actually connect to (``Request.host``), and never through a proxy.
    if req.has_proxy() or req.host.lower() not in (host, f"{host}:{HTTPS_PORT}"):
        raise blocked(req.host)


class _AllowlistGuard(urllib.request.BaseHandler):
    """``default_open`` runs before any protocol handler, for every request and redirect hop."""

    handler_order = 0

    def __init__(self, allowlist: Allowlist) -> None:
        self._allowlist = allowlist

    def default_open(self, req: urllib.request.Request) -> None:
        _check_urllib_request(self._allowlist, req)
        return None  # go on to https_open


class _AllowlistHTTPSHandler(urllib.request.HTTPSHandler):
    """The network handler; checks again right before connecting."""

    def __init__(self, allowlist: Allowlist) -> None:
        super().__init__(context=ssl.create_default_context())
        self._allowlist = allowlist

    def https_open(self, req: urllib.request.Request) -> Any:
        _check_urllib_request(self._allowlist, req)
        return super().https_open(req)


def build_allowlist_opener(
    allowlist: Allowlist = WORKER_ALLOWLIST,
    *,
    https_handler: urllib.request.BaseHandler | None = None,
) -> urllib.request.OpenerDirector:
    """An opener that only does ``https`` to ``allowlist``.

    Assembled by hand rather than with ``build_opener``, which would add the default ``http``,
    ``ftp``, ``file`` and ``data`` handlers and a ``ProxyHandler`` reading the environment.
    ``https_handler`` replaces the network handler in tests (no network); the guard still runs.
    """
    opener = urllib.request.OpenerDirector()
    handlers: tuple[urllib.request.BaseHandler, ...] = (
        # Empty: no proxy from the environment. With no proxies it registers no ``<scheme>_open``,
        # so OpenerDirector drops it, which is the point (a test checks no handler proxies).
        urllib.request.ProxyHandler({}),
        _AllowlistGuard(allowlist),
        https_handler if https_handler is not None else _AllowlistHTTPSHandler(allowlist),
        urllib.request.UnknownHandler(),
        urllib.request.HTTPDefaultErrorHandler(),
        urllib.request.HTTPRedirectHandler(),  # each hop goes through the guard again
        urllib.request.HTTPErrorProcessor(),  # 4xx and 5xx raise HTTPError, as the library expects
    )
    for handler in handlers:
        opener.add_handler(handler)
    return opener


def install_worker_allowlist() -> urllib.request.OpenerDirector:
    """Install the worker's opener for every ``urllib.request.urlopen`` in the process."""
    opener = build_allowlist_opener(WORKER_ALLOWLIST)
    urllib.request.install_opener(opener)
    return opener


# --- httpx ---------------------------------------------------------------------------------


class AllowlistTransport(httpx.AsyncBaseTransport):
    """Checks every request (each redirect hop is a request) before the inner transport."""

    def __init__(self, allowlist: Allowlist, inner: httpx.AsyncBaseTransport) -> None:
        self._allowlist = allowlist
        self._inner = inner

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        url = request.url
        host = url.raw_host.decode("ascii", errors="replace")
        self._allowlist.check(url.scheme, host, url.port, bool(url.userinfo))
        return await self._inner.handle_async_request(request)

    async def aclose(self) -> None:
        await self._inner.aclose()


def allowlisted_async_client(
    allowlist: Allowlist = WORKER_ALLOWLIST,
    *,
    timeout: float = MODEL_TIMEOUT_SECONDS,
    inner: httpx.AsyncBaseTransport | None = None,
) -> httpx.AsyncClient:
    """The ``httpx.AsyncClient`` for the model SDK (``AsyncAnthropic(http_client=...)``).

    ``trust_env=False`` on the client and the transport: no proxy, ``NETRC`` or ``SSL_CERT_*``
    from the environment. ``inner`` replaces the network transport in tests (an
    ``httpx.MockTransport``, no network); the check still runs.
    """
    network = inner if inner is not None else httpx.AsyncHTTPTransport(trust_env=False)
    return httpx.AsyncClient(
        transport=AllowlistTransport(allowlist, network),
        trust_env=False,
        timeout=timeout,
    )
