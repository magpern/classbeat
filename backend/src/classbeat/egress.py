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
  request, so every redirect hop too. The client does not follow redirects
  (``follow_redirects=False``, httpx's default): a redirect to another host is refused here, and
  a same-host one comes back to the SDK as a 3xx response, which the model provider maps to
  ``internal`` after one attempt (intended: the API never redirects). ``refused`` lets the SDK
  see a refusal as an error it does not retry (``classbeat.agent.providers.anthropic``).

Both paths verify TLS with a context from ``tls_client_context()``, which never writes a TLS key
log (``SSLKEYLOGFILE`` is ignored, threat model T49).

A refused request raises ``OutboundBlocked`` (the job fails ``outbound_blocked``, ADR 0005 §4)
and logs the host only, never the URL, its path or query, or a header.

The list is code, not a setting: nothing in the environment can widen it. This is enforced inside
the process; code running in the worker could still open a socket around it (accepted until the
network-level egress rule of M6, threat model).
"""

import logging
import ssl
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

import certifi
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
    the refused host (lower case, printable, at most 253 characters), never the URL.

    ``after_request`` is True when the refusal came on a redirect hop: the first request had
    already been sent, so whatever it asked the provider to do may have happened (an external
    operation is then ``uncertain``, never ``failed``). On the **urllib** path it is False only
    for a refusal before anything left the process. On the **httpx** path it is set only for a
    cross-host redirect; a same-host hop to ``http`` or another port is refused by
    ``Allowlist.check`` with False although the first request was sent, so an operation made
    over httpx must never be ``single_request`` or otherwise rely on this flag."""

    def __init__(self, host: str, *, after_request: bool = False) -> None:
        super().__init__("outbound request blocked")
        self.host = host
        self.after_request = after_request


def tls_client_context(*, cafile: str | None = None) -> ssl.SSLContext:
    """A verifying TLS client context, as ``ssl.create_default_context()`` builds it on Python
    3.12 (``PROTOCOL_TLS_CLIENT``: hostname check, ``CERT_REQUIRED``; ``cafile`` or the default
    trust store), but without the key log. ``create_default_context`` sets ``keylog_filename``
    from ``SSLKEYLOGFILE``, which opens that file at once and then appends every session's
    secrets to it, enough to decrypt a capture of the traffic (the model key, MOWL tokens). This
    context never opens one; the worker also refuses to start with ``anthropic`` while
    ``SSLKEYLOGFILE`` is set (``classbeat.config.MODEL_SDK_REFUSED_ENVIRONMENT``)."""
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    if cafile is None:
        context.load_default_certs(ssl.Purpose.SERVER_AUTH)
    else:
        context.load_verify_locations(cafile=cafile)
    return context


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
        parts = None
        port: int | None = None
        try:
            parts = urlsplit(url)
            port = parts.port
        except ValueError:
            parts = None
        if parts is None:
            # Outside the except block: no __context__ (the ValueError's text holds URL parts).
            raise blocked(None)
        has_userinfo = parts.username is not None or parts.password is not None
        return self.check(parts.scheme, parts.hostname, port, has_userinfo)


WORKER_ALLOWLIST = Allowlist(WORKER_HOSTS)

# Set by ``install_worker_allowlist``: this process is the worker. The web-only call sites the
# guard exempts (OAuth sign-in, account mail) call ``refuse_in_worker`` first, so they cannot
# reach the network from the worker even if worker code ends up calling them.
_worker_process = False


def worker_allowlist_installed() -> bool:
    return _worker_process


def refuse_in_worker(host: str | None) -> None:
    """For the web-only outbound call sites: raise ``OutboundBlocked`` inside the worker."""
    if _worker_process:
        raise blocked(host)


def blocked(host: str | None, *, after_request: bool = False) -> OutboundBlocked:
    """Log the refusal (host only) and return the exception to raise."""
    shown = _printable_host(host)
    logger.warning("outbound request blocked", extra={"host": shown})
    return OutboundBlocked(shown, after_request=after_request)


# --- urllib --------------------------------------------------------------------------------


def _check_urllib_request(allowlist: Allowlist, req: urllib.request.Request) -> None:
    try:
        host = allowlist.check_url(req.full_url)
        # What http.client will actually connect to (``Request.host``), never through a proxy.
        if req.has_proxy() or req.host.lower() not in (host, f"{host}:{HTTPS_PORT}"):
            raise blocked(req.host)
    except OutboundBlocked as refused:
        # urllib's redirect handler gives every hop after the first a ``redirect_dict``.
        refused.after_request = hasattr(req, "redirect_dict")
        raise


class _AllowlistGuard(urllib.request.BaseHandler):
    """``default_open`` runs before any protocol handler, for every request and redirect hop."""

    handler_order = 0

    def __init__(self, allowlist: Allowlist) -> None:
        self._allowlist = allowlist

    def default_open(self, req: urllib.request.Request) -> None:
        _check_urllib_request(self._allowlist, req)
        return None  # go on to https_open


def _same_host(old: str, new: str) -> bool:
    a, b = urlsplit(old), urlsplit(new)
    return (a.hostname or "").lower() == (b.hostname or "").lower()


class _SameHostRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Follows a redirect only to the same host: urllib copies every header (the library's
    ``authorization`` too) onto the next hop, so a hop to the other allowlisted host would hand
    one provider's token to the other. The new hop still goes through the guard."""

    def redirect_request(
        self,
        req: urllib.request.Request,
        fp: Any,
        code: int,
        msg: str,
        headers: Any,
        newurl: str,
    ) -> urllib.request.Request | None:
        if not _same_host(req.full_url, newurl):
            raise blocked(
                urlsplit(newurl).hostname if "://" in newurl else None, after_request=True
            )
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class _AllowlistHTTPSHandler(urllib.request.HTTPSHandler):
    """The network handler; checks again right before connecting."""

    def __init__(self, allowlist: Allowlist) -> None:
        super().__init__(context=tls_client_context())
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
        _SameHostRedirectHandler(),  # same host only; each hop goes through the guard again
        urllib.request.HTTPErrorProcessor(),  # 4xx and 5xx raise HTTPError, as the library expects
    )
    for handler in handlers:
        opener.add_handler(handler)
    return opener


def install_worker_allowlist() -> urllib.request.OpenerDirector:
    """Install the worker's opener for every ``urllib.request.urlopen`` in the process."""
    global _worker_process
    opener = build_allowlist_opener(WORKER_ALLOWLIST)
    urllib.request.install_opener(opener)
    _worker_process = True
    return opener


# --- httpx ---------------------------------------------------------------------------------


class AllowlistTransport(httpx.AsyncBaseTransport):
    """Checks every request (each redirect hop is a request) before the inner transport, and
    refuses a redirect response that points at another host: httpx drops ``Authorization`` on a
    cross-origin hop but keeps ``x-api-key``, so the model key must never follow one."""

    def __init__(
        self,
        allowlist: Allowlist,
        inner: httpx.AsyncBaseTransport,
        refused: Callable[[OutboundBlocked], Exception] | None = None,
    ) -> None:
        self._allowlist = allowlist
        self._inner = inner
        self._refused = refused

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        try:
            return await self._handle(request)
        except OutboundBlocked as exc:
            if self._refused is None:
                raise
            raise self._refused(exc) from exc

    async def _handle(self, request: httpx.Request) -> httpx.Response:
        url = request.url
        host = url.raw_host.decode("ascii", errors="replace")
        self._allowlist.check(url.scheme, host, url.port, bool(url.userinfo))
        response = await self._inner.handle_async_request(request)
        location = response.headers.get("location")
        if response.is_redirect and location is not None:
            target: httpx.URL | None
            try:
                target = url.join(location)
            except (httpx.InvalidURL, ValueError):
                target = None
            if target is None or target.raw_host.lower() != url.raw_host.lower():
                await response.aclose()
                shown = None if target is None else target.raw_host.decode("ascii", "replace")
                raise blocked(shown, after_request=True)
        return response

    async def aclose(self) -> None:
        await self._inner.aclose()


def model_network_transport() -> httpx.AsyncHTTPTransport:
    """The network transport under the allowlist: trust store ``certifi`` (the one httpx itself
    uses by default, not the system's), no TLS key log. ``trust_env=False`` is kept as a
    belt-and-braces flag: with our own ``verify`` context it no longer decides anything about
    TLS (``SSL_CERT_FILE`` and ``SSL_CERT_DIR`` are ignored because that context is passed, not
    because of the flag) and the transport has no proxy settings."""
    return httpx.AsyncHTTPTransport(
        trust_env=False, verify=tls_client_context(cafile=certifi.where())
    )


def allowlisted_async_client(
    allowlist: Allowlist = WORKER_ALLOWLIST,
    *,
    timeout: float = MODEL_TIMEOUT_SECONDS,
    inner: httpx.AsyncBaseTransport | None = None,
    refused: Callable[[OutboundBlocked], Exception] | None = None,
) -> httpx.AsyncClient:
    """The ``httpx.AsyncClient`` for the model SDK (``AsyncAnthropic(http_client=...)``).

    ``trust_env=False`` on the client: no proxy or ``NETRC`` from the environment. The
    environment's ``SSL_CERT_*`` is ignored because the transport is handed ``tls_client_context``
    (httpx's own trust store, ``certifi``) instead of building one itself, which also means no
    key log. ``inner`` replaces the network transport in tests (an
    ``httpx.MockTransport``, no network); the check still runs. ``refused`` turns an
    ``OutboundBlocked`` into the exception the caller's library does not retry (raised ``from``
    it, so the refusal stays its cause).
    """
    network = inner if inner is not None else model_network_transport()
    return httpx.AsyncClient(
        transport=AllowlistTransport(allowlist, network, refused),
        trust_env=False,
        timeout=timeout,
    )
