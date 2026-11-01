"""The outbound allowlists (ADR 0006 §9, ADR 0009 §10; threat model T19, T49, T73).

The only place in ``classbeat`` that builds an HTTP client or an ``urllib`` opener (the guard in
``tests/egress_scan.py`` fails on any other). Two paths leave the worker:

- **urllib**, used by the provider library (``mowl-workouts`` calls ``urllib.request.urlopen``).
  The web process installs the same opener with ``WEB_URLLIB_HOSTS``, MOWL, the Spotify API and
  Swedebeat's sign-in host (``install_web_allowlist``, SR-03): its library calls are MOWL's
  connect, the playlist listing and the save/update/attach checks, the Spotify reads of Check
  again and the Swedebeat sign-in (ADR 0015).
  ``install_worker_allowlist()`` installs, before anything else in the worker, a process-wide
  opener with an empty ``ProxyHandler`` (no ``HTTPS_PROXY`` or ``NO_PROXY`` setting reroutes a
  call) and a guard that refuses every request, redirect hops included, that is not ``https`` to
  an allowlisted host on port 443. It has no ``http``, ``ftp``, ``file`` or ``data`` handler.
  **A redirect is followed only to the same host** (``_SameHostRedirectHandler``): urllib copies
  every header, the library's ``authorization`` included, onto the next hop, so a hop to any
  other host, allowlisted or not, is refused before it is sent (``OutboundBlocked`` with
  ``after_request``). A same-host hop keeps the header (it is the host the token was meant for),
  but must still be ``https`` on port 443 and passes the guard again.
- **httpx**, for the model client (A2): ``allowlisted_async_client()`` returns an
  ``httpx.AsyncClient`` with ``trust_env=False`` whose transport applies the same check to every
  request, so every redirect hop too. The client does not follow redirects
  (``follow_redirects=False``, httpx's default): a redirect to another host is refused here, and
  a same-host one comes back to the SDK as a 3xx response, which the model provider maps to
  ``internal`` after one attempt (intended: the API never redirects). ``refused`` lets the SDK
  see a refusal as an error it does not retry (``classbeat.agent.providers.anthropic``). Its
  list is its provider's one host (``MODEL_ALLOWLISTS``, ADR 0012 §2): an OpenAI client can never
  reach DeepSeek's host or the other way round, although both use the ``openai`` SDK.

**Model hosts on the worker list** (ADR 0012 §2): ``MODEL_HOSTS`` names each provider's one API
host. The worker's urllib list (``worker_allowlist``) holds a model host only for a provider the
worker can use (its key file present; DeepSeek also needs ``CLASSBEAT_DEEPSEEK_ENABLED``), which
``classbeat.config.worker_model_providers`` decides from the settings.

**The Spotify client** (ADR 0009 §10): ``spotify_async_client()`` is the ``httpx.AsyncClient`` of
``classbeat.spotify.http`` (the token endpoint and ``GET /me``), in the web process (connect) and
the worker (refresh) alike. Its list is ``SPOTIFY_ALLOWLIST``, the two Spotify hosts and nothing
else, in either process; it does not follow redirects and refuses one to another host; it has
``trust_env=False``; and its request has a **total** deadline covering the answer's body
(``total_seconds``; a trickling answer raises ``RequestDeadline``). No URL from a Spotify answer
(``next``, ``href``) is ever fetched; were one handed to it, the list refuses any other host.

**The media client** (ADR 0016 §4, threat model T109): ``mowl_media_async_client()`` fetches a
Swedebeat mix from the temporary link MOWL returns (A3, worker only). Its list is
``MOWL_MEDIA_ALLOWLIST``, built from the literal ``MOWL_MEDIA_HOSTS``, which is **empty until the
owner confirms the host**; so is nothing else's: the library's MOWL opener, with its
``authorization`` header, cannot reach the media host (it is on no urllib list), and this client
sends none of our headers. It follows no redirect at all, ends every request within
``MOWL_MEDIA_TOTAL_SECONDS`` and takes a list as an argument only from tests (the guard refuses it
in ``classbeat``). Swedebeat's embedded AWS credentials are never used (a static guard).
``cloud.swedebeat.se`` (B1) will get its own scoped list and client the same way.

All paths verify TLS with a context from ``tls_client_context()``, which never writes a TLS key
log (``SSLKEYLOGFILE`` is ignored, threat model T49).

**A total deadline per urllib request** (ADR 0007 section 6). ``urlopen(timeout=30)`` is only a
socket timeout per blocking operation, so a response trickling in a byte at a time never ends
on its own. The worker's network handler (``DeadlineHTTPSHandler``) gives every request a total
deadline: its ``timeout`` (``REQUEST_FALLBACK_SECONDS`` when the caller gave none), and never
later than the thread's ``call_deadline`` if one is set. A watchdog shuts the socket down when
it passes, so a blocked read returns at once and the request raises ``RequestDeadline`` (a
``TimeoutError``); a request that would start after the deadline is refused before anything is
sent. A multi-request write runs inside ``call_deadline_until(at)``, ``at`` being taken before
its ``pending`` row is written plus ``bound_seconds``, so no request of it outlives its bound
(``call_may_be_running`` rests on that), however late its thread starts. The one wait it cannot
cut is a DNS lookup in progress, which the resolver's own timeout ends; the request is refused
right after.

A refused request raises ``OutboundBlocked`` (the job fails ``outbound_blocked``, ADR 0005 §4)
and logs the host only, never the URL, its path or query, or a header.

The list is code, not a setting: nothing in the environment can widen it. This is enforced inside
the process; code running in the worker could still open a socket around it (accepted until the
network-level egress rule of M6, threat model).
"""

import asyncio
import contextlib
import http.client
import logging
import math
import socket
import ssl
import threading
import time
import urllib.request
from collections.abc import AsyncIterator, Callable, Iterable, Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Final, Literal
from urllib.parse import urlsplit

import certifi
import httpx

logger = logging.getLogger(__name__)

HTTPS = "https"
HTTPS_PORT = 443
MOWL_HOST = "api.mowl.com"  # the library's base URL
# Swedebeat (ADR 0015): the web process signs a user in at ``www.swedebeat.se`` (the library's
# ``downloader/login.php``). ``cloud.swedebeat.se`` (the catalogue, where the token is also sent)
# is on no list: nothing in classbeat calls it yet, and it joins a list when a feature does (B1 of
# ADR 0016, with its own scoped list and client; never on ``WORKER_HOSTS``).
SWEDEBEAT_WWW_HOST = "www.swedebeat.se"
# The host(s) of the MOWL temporary links that serve a Swedebeat mix's audio (ADR 0016 §4). Exact
# lower case DNS names, https on 443, no wildcard and never a whole cloud provider domain. **Empty
# until the owner confirms the host in the live check** (a reviewed PR then fills it): with an empty
# list the media fetch is refused for every host. A literal, so nothing in the environment, a file
# or a setting can widen it (a guard test pins that and that no other source feeds the list). It is
# on no urllib list: ``WORKER_HOSTS`` and ``WEB_URLLIB_HOSTS`` never hold it, so the library's MOWL
# opener (and its ``authorization`` header) cannot reach it.
MOWL_MEDIA_HOSTS: Final[frozenset[str]] = frozenset()
# The model providers classbeat may use (ADR 0012 §1); ``fake`` is not one (no host).
ModelProviderName = Literal["anthropic", "openai", "deepseek"]
# Each provider's one API host (ADR 0012 §2). Code, not a setting.
MODEL_HOSTS: Final[Mapping[ModelProviderName, str]] = MappingProxyType(
    {"anthropic": "api.anthropic.com", "openai": "api.openai.com", "deepseek": "api.deepseek.com"}
)
MODEL_HOST = MODEL_HOSTS["anthropic"]
SPOTIFY_API_HOST = "api.spotify.com"
SPOTIFY_ACCOUNTS_HOST = "accounts.spotify.com"  # the authorize page and the token endpoint
SPOTIFY_HOSTS = frozenset({SPOTIFY_API_HOST, SPOTIFY_ACCOUNTS_HOST})
# The worker's urllib hosts without a model host: the MOWL API (M3a) and the Spotify API and
# token endpoint (M4, ADR 0009 §10), which the library reaches over urllib. A model host joins
# them only for a provider the worker can use (``worker_hosts``, ADR 0012 §2).
WORKER_HOSTS = frozenset({MOWL_HOST}) | SPOTIFY_HOSTS
# The web's urllib hosts (SR-03, #353): the provider library's calls in the web process are
# MOWL's (connect, ``GET /api/music/mowl-playlists``, the save/update/attach checks) and the
# Spotify API's (the user's Check again of a playlist create or replace reads through
# ``spotify_web``). Not ``accounts.spotify.com``: classbeat never lets the library refresh a
# token (``spotify_refresh_enabled=False``), the httpx Spotify client does. Swedebeat's sign-in
# host joins them for the Swedebeat connect (ADR 0015); the sign-in sends the user's encoded
# email and password there and receives the token, and sends no token. Not ``cloud.swedebeat.se``,
# not a model host. Each token stays on its own host: a redirect is followed to the same host only.
WEB_URLLIB_HOSTS = frozenset({MOWL_HOST, SPOTIFY_API_HOST, SWEDEBEAT_WWW_HOST})
MODEL_TIMEOUT_SECONDS = 120.0  # ADR 0006 §2
# The Spotify client's total deadline per request unless the caller gives one (ADR 0009 §6:
# ``CLASSBEAT_SPOTIFY_REFRESH_TIMEOUT_SECONDS`` defaults to 10).
SPOTIFY_TOTAL_SECONDS = 10.0
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
    context never opens one; the worker also refuses to start with a real model provider while
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
WEB_URLLIB_ALLOWLIST = Allowlist(WEB_URLLIB_HOSTS)
# Each model client's list: its provider's one host, although the worker's urllib list is wider.
MODEL_ALLOWLISTS: Final[Mapping[ModelProviderName, Allowlist]] = MappingProxyType(
    {provider: Allowlist(frozenset({host})) for provider, host in MODEL_HOSTS.items()}
)
MODEL_ALLOWLIST = MODEL_ALLOWLISTS["anthropic"]


def worker_hosts(model_providers: Iterable[str]) -> frozenset[str]:
    """The worker's urllib hosts with the model host of each of ``model_providers`` (the ones
    the worker can use, ``classbeat.config.worker_model_providers``). An unknown name is
    refused, never ignored."""
    hosts = set(WORKER_HOSTS)
    for provider in model_providers:
        if provider not in MODEL_HOSTS:
            raise ValueError("unknown model provider")
        hosts.add(MODEL_HOSTS[provider])  # pyright: ignore[reportArgumentType]
    return frozenset(hosts)


def worker_allowlist(model_providers: Iterable[str] = ()) -> Allowlist:
    """The worker's urllib list for these usable model providers (ADR 0012 §2)."""
    return Allowlist(worker_hosts(model_providers))


# The Spotify client's list, in the web process and the worker alike (ADR 0009 §10).
SPOTIFY_ALLOWLIST = Allowlist(SPOTIFY_HOSTS)
# The media client's list (ADR 0016 §4): ``MOWL_MEDIA_HOSTS``, empty today.
MOWL_MEDIA_ALLOWLIST = Allowlist(MOWL_MEDIA_HOSTS)

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


# --- the per-job request budget (ADR 0009 section 8) -------------------------------------------


class RequestBudgetExhausted(Exception):
    """A request to a budgeted host would have gone over its job's budget, so it was **never
    sent** (``CLASSBEAT_SPOTIFY_CALLS_PER_JOB``). No text, no host, no count."""

    def __init__(self) -> None:
        super().__init__("request budget used up")


class RequestBudget:
    """How many requests to ``hosts`` one job may still make: ``limit`` in all, ``used`` of them
    already (across its stages and attempts; the runner reads and stores ``used``). Every
    request and every redirect hop to one of the hosts takes one before it is sent; the one that
    would go over ``limit`` raises ``RequestBudgetExhausted`` instead. Thread-safe: the library's
    calls run in worker threads (``asyncio.to_thread`` carries the context)."""

    def __init__(self, hosts: frozenset[str], limit: int, used: int = 0) -> None:
        if limit < 0 or used < 0:
            raise ValueError("a request budget is not negative")
        self.hosts = hosts
        self.limit = limit
        self._used = used
        self._lock = threading.Lock()

    @property
    def used(self) -> int:
        with self._lock:
            return self._used

    def take(self, host: str) -> None:
        if host.lower() not in self.hosts:
            return
        with self._lock:
            if self._used >= self.limit:
                raise RequestBudgetExhausted
            self._used += 1


_request_budget: ContextVar[RequestBudget | None] = ContextVar("request_budget", default=None)


@contextmanager
def request_budget(budget: RequestBudget) -> Iterator[RequestBudget]:
    """Inside the block (and in threads started from it with the context, as ``asyncio.to_thread``
    does), every urllib and ``spotify_async_client`` request to the budget's hosts is counted."""
    token = _request_budget.set(budget)
    try:
        yield budget
    finally:
        _request_budget.reset(token)


def _spend(host: str | None) -> None:
    budget = _request_budget.get()
    if budget is not None and host is not None:
        budget.take(host)


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
        _spend(urlsplit(req.full_url).hostname)  # counted once per request or hop, before sending
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


# --- the total deadline of one urllib request ------------------------------------------------

# A request whose caller gave no timeout (urllib's default) still ends within this.
REQUEST_FALLBACK_SECONDS: Final = 60.0
_thread_deadline = threading.local()


class RequestDeadline(TimeoutError):
    """A request outlived its total deadline (or would have started after it). No text."""

    def __init__(self) -> None:
        super().__init__("request deadline passed")


@contextmanager
def call_deadline(seconds: float) -> Iterator[None]:
    """Inside the block, every urllib request this thread makes ends by ``now + seconds``: one
    still running then is cut off, and a later one is refused before it is sent. Nested blocks
    keep the earlier deadline."""
    if not seconds > 0:
        raise ValueError("a call deadline is positive")
    with call_deadline_until(time.monotonic() + seconds):
        yield


@contextmanager
def call_deadline_until(at: float) -> Iterator[None]:
    """``call_deadline`` for an absolute ``time.monotonic()`` instant taken by the caller
    **before** the call was scheduled (a multi-request write takes it before its ``pending``
    row is written). A thread that starts late uses up the time instead of extending it: once
    ``at`` has passed every request is refused before it is sent."""
    if not isinstance(at, int | float) or isinstance(at, bool):
        raise TypeError("a call deadline is a monotonic instant")
    if not math.isfinite(at):  # nan would compare false everywhere and never expire
        raise ValueError("a call deadline is a finite instant")
    previous: float | None = getattr(_thread_deadline, "at", None)
    _thread_deadline.at = float(at) if previous is None else min(previous, float(at))
    try:
        yield
    finally:
        _thread_deadline.at = previous


def request_deadline(timeout: object) -> float:
    """The monotonic time by which a request with this ``timeout`` must be over."""
    total = REQUEST_FALLBACK_SECONDS
    if isinstance(timeout, int | float) and not isinstance(timeout, bool) and timeout > 0:
        total = float(timeout)
    at = time.monotonic() + total
    call: float | None = getattr(_thread_deadline, "at", None)
    return at if call is None else min(at, call)


class _Watchdog:
    """Shuts the request's socket down at its deadline. It holds a duplicate of the socket
    (``shutdown`` acts on the connection, whatever descriptor it is called on, so it also cuts
    a TLS socket wrapped around the original), closed when the request is over."""

    def __init__(self, at: float) -> None:
        self.at = at
        self.expired = False
        self._lock = threading.Lock()
        self._sock: socket.socket | None = None
        self._timer: threading.Timer | None = None
        self._done = False

    def check(self) -> None:
        if self.expired or time.monotonic() >= self.at:
            self.expired = True
            raise RequestDeadline

    def arm(self, sock: socket.socket) -> None:
        with self._lock:
            if self._done:
                return
            self._sock = sock.dup()
            timer = threading.Timer(max(0.0, self.at - time.monotonic()), self._fire)
            timer.daemon = True
            self._timer = timer
            timer.start()

    def _fire(self) -> None:
        with self._lock:
            if self._done:
                return
            self.expired = True
            self._release(shutdown=True)

    def disarm(self) -> None:
        with self._lock:
            if self._timer is not None:
                self._timer.cancel()
            self._release(shutdown=False)

    def _release(self, *, shutdown: bool) -> None:  # with the lock held
        self._done = True
        sock, self._sock = self._sock, None
        if sock is None:
            return
        if shutdown:
            with contextlib.suppress(OSError):  # closed already
                sock.shutdown(socket.SHUT_RDWR)
        sock.close()

    def guard[T](self, read: Callable[[], T]) -> T:
        """A read of the response: past the deadline it raises ``RequestDeadline`` (also when
        the cut-off made the read fail or come back short)."""
        try:
            out = read()
        except Exception:
            if self.expired:
                raise RequestDeadline from None
            raise
        self.check()
        return out


class _DeadlineResponse(http.client.HTTPResponse):
    """A response whose reads honour the request's deadline; closing it ends the watchdog."""

    _watchdog: _Watchdog | None = None

    def watch(self, watchdog: _Watchdog) -> None:
        self._watchdog = watchdog

    def _guarded[T](self, read: Callable[[], T]) -> T:
        return read() if self._watchdog is None else self._watchdog.guard(read)

    def read(self, amt: int | None = None) -> bytes:
        return self._guarded(lambda: super(_DeadlineResponse, self).read(amt))

    def read1(self, n: int = -1) -> bytes:
        return self._guarded(lambda: super(_DeadlineResponse, self).read1(n))

    def readinto(self, b: Any) -> int:
        return self._guarded(lambda: super(_DeadlineResponse, self).readinto(b))

    def readline(self, limit: int = -1) -> bytes:
        return self._guarded(lambda: super(_DeadlineResponse, self).readline(limit))

    def peek(self, n: int = -1) -> bytes:
        return self._guarded(lambda: super(_DeadlineResponse, self).peek(n))

    def close(self) -> None:
        try:
            super().close()
        finally:
            if self._watchdog is not None:
                self._watchdog.disarm()


class DeadlineHTTPSConnection(http.client.HTTPSConnection):
    """``HTTPSConnection`` with the total deadline of its one request (urllib makes a
    connection per request): refused before connecting when it has passed, the watchdog armed
    as soon as the socket exists, and handed to the response."""

    response_class = _DeadlineResponse

    def __init__(self, host: str, *args: Any, **kwargs: Any) -> None:
        self._watchdog = _Watchdog(request_deadline(kwargs.get("timeout")))
        self._watchdog.check()  # nothing is sent after the deadline
        self._handing_over = False
        super().__init__(host, *args, **kwargs)
        create = self._create_connection

        def watched(*a: Any, **k: Any) -> socket.socket:
            sock = create(*a, **k)
            self._watchdog.arm(sock)
            return sock

        self._create_connection = watched

    def connect(self) -> None:
        try:
            super().connect()
            self._watchdog.check()  # the handshake ended past the deadline: send nothing
        except Exception:
            if self._watchdog.expired:
                raise RequestDeadline from None
            raise

    def send(self, data: Any) -> None:
        try:
            super().send(data)
        except Exception:
            if self._watchdog.expired:
                raise RequestDeadline from None
            raise

    def getresponse(self) -> http.client.HTTPResponse:
        # ``getresponse`` closes the connection itself when the response will close it, which
        # hands the socket to the response: the watchdog then ends with the response.
        self._handing_over = True
        try:
            response = super().getresponse()
        except Exception:
            self._watchdog.disarm()
            if self._watchdog.expired:
                raise RequestDeadline from None
            raise
        if isinstance(response, _DeadlineResponse):
            response.watch(self._watchdog)
        else:  # pragma: no cover - response_class is ours
            self._watchdog.disarm()
        return response

    def close(self) -> None:
        try:
            super().close()
        finally:
            if not self._handing_over:
                self._watchdog.disarm()


class DeadlineHTTPSHandler(urllib.request.HTTPSHandler):
    """urllib's HTTPS handler with ``DeadlineHTTPSConnection``: every request has a total
    deadline (module docstring)."""

    def https_open(self, req: urllib.request.Request) -> Any:
        return self.do_open(DeadlineHTTPSConnection, req, context=self._context)  # pyright: ignore[reportAttributeAccessIssue]


class _AllowlistHTTPSHandler(DeadlineHTTPSHandler):
    """The network handler; checks again right before connecting. Each request has its total
    deadline (``DeadlineHTTPSHandler``)."""

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


def install_worker_allowlist(model_providers: Iterable[str] = ()) -> urllib.request.OpenerDirector:
    """Install the worker's opener for every ``urllib.request.urlopen`` in the process.

    ``model_providers`` are the providers the worker can use (``worker_model_providers``); their
    hosts join the list. Without them (before the settings are read) it holds no model host."""
    global _worker_process
    opener = build_allowlist_opener(worker_allowlist(model_providers))
    urllib.request.install_opener(opener)
    _worker_process = True
    return opener


def install_web_allowlist() -> urllib.request.OpenerDirector:
    """Install the web process's urllib opener (SR-03, threat model T19): the worker's opener
    (``https`` to port 443 only, no environment proxy, every redirect hop checked, a redirect
    only to the same host) with the list ``WEB_URLLIB_ALLOWLIST`` (``api.mowl.com``,
    ``api.spotify.com`` and ``www.swedebeat.se``; a token never follows a redirect from one to
    another).

    Unlike ``install_worker_allowlist`` it does not set the worker flag: the web-only call sites
    (OAuth sign-in, account mail) keep working. Called once by ``python -m classbeat`` before the
    app is built; tests that run the app in-process do not install it. The operator commands
    (``invite``, ``admin``, ``jobs``, ``keys``, ``models``) make no outbound request, so they do
    not install it. An outbound proxy from the environment is no longer possible for the
    library's calls (and, since ``trust_env=False``, for OAuth sign-in either)."""
    opener = build_allowlist_opener(WEB_URLLIB_ALLOWLIST)
    urllib.request.install_opener(opener)
    return opener


# --- httpx ---------------------------------------------------------------------------------


class _DeadlineStream(httpx.AsyncByteStream):
    """An answer's body that must be read by ``at`` (``time.monotonic()``): each chunk waits at
    most until then, so a body trickling in a byte at a time ends with ``RequestDeadline``."""

    def __init__(self, inner: httpx.AsyncByteStream, at: float) -> None:
        self._inner = inner
        self._at = at

    async def __aiter__(self) -> AsyncIterator[bytes]:
        chunks = self._inner.__aiter__()
        while True:
            remaining = self._at - time.monotonic()
            if remaining <= 0:
                raise RequestDeadline
            try:
                async with asyncio.timeout(remaining):
                    chunk = await anext(chunks)
            except StopAsyncIteration:
                return
            except TimeoutError:
                raise RequestDeadline from None
            yield chunk

    async def aclose(self) -> None:
        await self._inner.aclose()


class AllowlistTransport(httpx.AsyncBaseTransport):
    """Checks every request (each redirect hop is a request) before the inner transport, and
    refuses a redirect response that points at another host: httpx drops ``Authorization`` on a
    cross-origin hop but keeps ``x-api-key``, so the model key must never follow one.

    With ``total_seconds`` each request, its answer's body included, ends within that time
    (``RequestDeadline``, a ``TimeoutError``): httpx's own timeouts are per read, so they never
    end an answer that keeps trickling in."""

    def __init__(
        self,
        allowlist: Allowlist,
        inner: httpx.AsyncBaseTransport,
        refused: Callable[[OutboundBlocked], Exception] | None = None,
        *,
        total_seconds: float | None = None,
    ) -> None:
        if total_seconds is not None and not total_seconds > 0:
            raise ValueError("a total deadline is positive")
        self._allowlist = allowlist
        self._inner = inner
        self._refused = refused
        self._total_seconds = total_seconds

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        try:
            if self._total_seconds is None:
                return await self._handle(request)
            return await self._handle_within(request, time.monotonic() + self._total_seconds)
        except OutboundBlocked as exc:
            if self._refused is None:
                raise
            raise self._refused(exc) from exc

    async def _handle_within(self, request: httpx.Request, at: float) -> httpx.Response:
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
            stream=_DeadlineStream(stream, at),
            extensions=response.extensions,
            request=request,
        )

    async def _handle(self, request: httpx.Request) -> httpx.Response:
        url = request.url
        host = url.raw_host.decode("ascii", errors="replace")
        self._allowlist.check(url.scheme, host, url.port, bool(url.userinfo))
        _spend(host)  # before sending; the model hosts are never in a budget
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
    *,
    provider: ModelProviderName = "anthropic",
    timeout: float = MODEL_TIMEOUT_SECONDS,
    inner: httpx.AsyncBaseTransport | None = None,
    refused: Callable[[OutboundBlocked], Exception] | None = None,
) -> httpx.AsyncClient:
    """The ``httpx.AsyncClient`` for the model SDK (``AsyncAnthropic(http_client=...)``).

    Its list is ``provider``'s one host (``MODEL_ALLOWLISTS``); a caller names the provider and
    never hands in a list (the guard refuses one), so no caller can widen it (ADR 0012 §2).
    ``trust_env=False`` on the client: no proxy or ``NETRC`` from the environment. The
    environment's ``SSL_CERT_*`` is ignored because the transport is handed ``tls_client_context``
    (httpx's own trust store, ``certifi``) instead of building one itself, which also means no
    key log. ``inner`` replaces the network transport in tests (an
    ``httpx.MockTransport``, no network); the check still runs. ``refused`` turns an
    ``OutboundBlocked`` into the exception the caller's library does not retry (raised ``from``
    it, so the refusal stays its cause).
    """
    allowlist = MODEL_ALLOWLISTS.get(provider)
    if allowlist is None:
        raise ValueError("unknown model provider")
    network = inner if inner is not None else model_network_transport()
    return httpx.AsyncClient(
        transport=AllowlistTransport(allowlist, network, refused),
        trust_env=False,
        timeout=timeout,
    )


def spotify_network_transport() -> httpx.AsyncHTTPTransport:
    """The network transport of the Spotify client: the trust store of the urllib path
    (``tls_client_context()``, the system's, as the library's calls to Spotify use), no TLS key
    log, no proxy."""
    return httpx.AsyncHTTPTransport(trust_env=False, verify=tls_client_context())


def spotify_async_client(
    *,
    total_seconds: float = SPOTIFY_TOTAL_SECONDS,
    inner: httpx.AsyncBaseTransport | None = None,
) -> httpx.AsyncClient:
    """The ``httpx.AsyncClient`` of ``classbeat.spotify.http`` (ADR 0009 §6, §10): the token
    endpoint and ``GET /me``, from the web process and the worker.

    Its list is always ``SPOTIFY_ALLOWLIST`` (no parameter widens it); ``trust_env=False`` (no
    proxy or ``NETRC`` from the environment); redirects are not followed and one to another host
    is refused (``OutboundBlocked`` with ``after_request``); every request with its body ends
    within ``total_seconds`` (``RequestDeadline``). ``inner`` replaces the network transport in
    tests (the fake Spotify, no network); the checks still run.
    """
    network = inner if inner is not None else spotify_network_transport()
    return httpx.AsyncClient(
        transport=AllowlistTransport(SPOTIFY_ALLOWLIST, network, total_seconds=total_seconds),
        trust_env=False,
        timeout=total_seconds,
        follow_redirects=False,
    )


# The media fetch's total time, body included (ADR 0016 §4: 300 s).
MOWL_MEDIA_TOTAL_SECONDS: Final = 300.0


def mowl_media_network_transport() -> httpx.AsyncHTTPTransport:
    """The network transport of the media client: the system trust store (as the urllib path),
    no TLS key log, no proxy from the environment."""
    return httpx.AsyncHTTPTransport(trust_env=False, verify=tls_client_context())


def mowl_media_async_client(
    allowlist: Allowlist = MOWL_MEDIA_ALLOWLIST,
    *,
    total_seconds: float = MOWL_MEDIA_TOTAL_SECONDS,
    inner: httpx.AsyncBaseTransport | None = None,
) -> httpx.AsyncClient:
    """The dedicated client that fetches a Swedebeat mix from a MOWL temporary link (ADR 0016 §4,
    A3 uses it in the worker; threat model T109).

    It reaches only ``allowlist`` (``MOWL_MEDIA_ALLOWLIST`` unless a test hands in its own: the
    production code under ``classbeat`` never passes one, the guard refuses it; the list is code
    and today empty, so every request is refused ``OutboundBlocked``), https on 443, no userinfo.
    It follows **no redirect at all** (``follow_redirects=False``): a hop to another host is
    refused (``OutboundBlocked`` with ``after_request``) and a same-host 3xx comes back as a
    response for the caller to refuse. ``trust_env=False`` (no proxy, ``NETRC`` or cookie jar from
    the environment), and **no default header**: httpx's ``User-Agent``, ``Accept`` and
    ``Accept-Encoding`` are cleared, so a request carries only what the call site sets (never the
    MOWL ``authorization``) and the answer is not compressed (``Content-Length`` and the streamed
    count mean the bytes on disk). Every request, its body included, ends within ``total_seconds``
    (``RequestDeadline``). ``inner`` replaces the network transport in tests (the fake media
    endpoint, no network); the checks still run.
    """
    network = inner if inner is not None else mowl_media_network_transport()
    client = httpx.AsyncClient(
        transport=AllowlistTransport(allowlist, network, total_seconds=total_seconds),
        trust_env=False,
        timeout=total_seconds,
        follow_redirects=False,
    )
    client.headers.clear()
    return client
