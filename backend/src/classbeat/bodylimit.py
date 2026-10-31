"""A cap on the request body of every web route (M6 security review SR-01, #351; threat model T103).

``BodyLimit`` is a plain ASGI middleware, installed inside ``SecurityHeaders`` (so its refusal
carries the security headers) and outside ``AuthMiddleware`` and everything else, so a body is
refused before any session, CSRF or database work and an anonymous caller cannot make the process
buffer more than ``DEFAULT_BODY_LIMIT`` of it (one chunk more, at most):

- a ``Content-Length`` over the limit (or not a plain number) is answered 413 without reading a
  byte and without the app running;
- every other body is counted as it streams in, whatever its ``Content-Length`` says (a chunked
  body has none, a lying one is not believed). Over the limit, the app is told the client is gone
  (``http.disconnect``), whatever it then answers is dropped, and the 413 is sent instead.

The answer is fixed: status 413, ``Connection: close``, ``Cache-Control: no-store`` and the body
``{"detail":{"reason":"body_too_large"}}``; it never names the route, the size or the limit.
GET, HEAD and other requests without a body are unaffected (no byte is read, so none is counted),
and so is the SSE stream (a GET). WebSocket and lifespan scopes pass through.

The limit is 64 KiB for every route: the largest body any route takes today is the job create's
input (16 KiB, ``jobs.routes.INPUT_MAX_BYTES``), and the jobs API and the admin area keep their
own, equal, caps (``jobs.routes.BODY_MAX_BYTES``, ``admin.routes.MAX_BODY_BYTES``) as a second
line. A route that legitimately needs more gets an entry in ``ROUTE_LIMITS`` (exact path to
bytes) with the reason next to it; there is none. The test suite fails if the table is not empty
without its entry being documented here.

``/api/admin`` is deferred to ``admin.routes.AdminBodyLimit``: its contract is that the gate runs
first (a non-admin gets 403, an admin's refusal gets an audit row) and the 413 ``body_too_large``
or 400 ``body_unreadable`` follows, which needs the app to run. That limiter caps what the admin
area reads at 64 KiB as well, so an anonymous caller still buffers no more than that.

A reverse proxy in front of the app needs the same cap (Caddy: ``request_body max_size``; nginx and
SWAG: ``client_max_body_size``): docs/runbooks/vps-deploy.md, "Request body size".
"""

import contextlib
import logging
import time
from collections.abc import Mapping
from typing import Any, Final

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from classbeat.auth import bucket_ip, client_ip
from classbeat.headers import CONTENT_SECURITY_POLICY, REFERRER_POLICY

DEFAULT_BODY_LIMIT: Final = 64 * 1024
# Exact path -> bytes, for a route that needs a larger body. Empty: no route does.
ROUTE_LIMITS: Final[Mapping[str, int]] = {}
# Paths whose own, equal, limiter answers (see the module docstring).
DEFERRED_PREFIXES: Final = ("/api/admin",)
BODY_TOO_LARGE: Final = "body_too_large"
logger = logging.getLogger(__name__)
# One log line per client address per this many seconds (a flood of oversized bodies must not flood
# the log); the line after a quiet spell says how many it stood for. An IPv4 address is its own key,
# an IPv6 one is keyed by its /64 (``auth.bucket_ip``: one holder of a /64 owns 2**64 addresses and
# must not log once per address or flush the table); the line still names the address itself. At
# most ``_LOG_TRACKED`` keys are remembered (the table is cleared when full: a flood from many
# addresses logs more, never grows without bound).
LOG_INTERVAL_SECONDS: Final = 10.0
_LOG_TRACKED: Final = 4096
_logged: dict[str, tuple[float, int]] = {}
_REFUSAL_BODY: Final = b'{"detail":{"reason":"body_too_large"}}'


def _deferred(path: str) -> bool:
    return any(path == p or path.startswith(p + "/") for p in DEFERRED_PREFIXES)


def _declared_too_large(scope: Scope, limit: int) -> bool:
    """A ``Content-Length`` over ``limit``, or one that is not a plain number (a proxy or server
    that lets one through still gets a refusal here)."""
    for name, value in scope.get("headers", []):
        if name.lower() == b"content-length" and (not value.isdigit() or int(value) > limit):
            return True
    return False


def _chunked(scope: Scope) -> bool:
    names = {name.lower() for name, _ in scope.get("headers", [])}
    return b"content-length" not in names and b"transfer-encoding" in names


async def _read_up_to(receive: Receive, limit: int) -> list[Message] | None:
    """The request messages of a body of at most ``limit`` bytes, or ``None`` when it is longer
    (nothing past the first chunk over the limit is read). A client that goes away mid-body ends
    up with what it sent: the app then sees the disconnect as it would have."""
    messages: list[Message] = []
    size = 0
    while True:
        message = await receive()
        messages.append(message)
        if message["type"] != "http.request":
            return messages
        size += len(message.get("body", b""))
        if size > limit:
            return None
        if not message.get("more_body", False):
            return messages


def _replay(messages: list[Message], receive: Receive) -> Receive:
    pending = list(messages)

    async def replayed() -> Message:
        return pending.pop(0) if pending else await receive()

    return replayed


def _client(scope: Scope) -> str:
    """The client address as the rest of the app reads it (``auth.deps.request_ip``): the peer,
    or behind a trusted proxy the right-most ``X-Forwarded-For`` entry that is not one."""
    app = scope.get("app")
    settings: Any = getattr(getattr(app, "state", None), "settings", None)
    networks = settings.trusted_proxy_networks if settings is not None else ()
    peer = scope.get("client")
    forwarded = [
        v.decode("latin-1") for k, v in scope.get("headers", []) if k == b"x-forwarded-for"
    ]
    return client_ip(peer[0] if peer else None, forwarded, networks)


def _log_refusal(scope: Scope) -> None:
    """A rate-limited line for a refused body: method, path without the query string, status and
    client address. Never the body, a header, the query or a cookie."""
    ip = _client(scope)
    key = bucket_ip(ip)
    now = time.monotonic()
    last, suppressed = _logged.get(key, (-LOG_INTERVAL_SECONDS, 0))
    if now - last < LOG_INTERVAL_SECONDS:
        _logged[key] = (last, suppressed + 1)
        return
    if len(_logged) >= _LOG_TRACKED:
        _logged.clear()
    _logged[key] = (now, 0)
    logger.warning(
        "request body refused",
        extra={
            "method": str(scope.get("method", "")),
            "path": str(scope.get("path", "")),
            "status": 413,
            "client_ip": ip,
            "suppressed": suppressed,
        },
    )


async def _refuse(scope: Scope, send: Send) -> None:
    # A handler or filter that raises (a full disk, a broken sink) must never turn the 413 into a
    # 500: the refusal is the contract, the line is best effort.
    with contextlib.suppress(Exception):
        _log_refusal(scope)
    await send(
        {
            "type": "http.response.start",
            "status": 413,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(_REFUSAL_BODY)).encode()),
                (b"cache-control", b"no-store"),
                (b"connection", b"close"),
                # SecurityHeaders (outside this middleware) sets these on every response, but this
                # answer does not depend on it being installed.
                (b"content-security-policy", CONTENT_SECURITY_POLICY.encode()),
                (b"referrer-policy", REFERRER_POLICY.encode()),
            ],
        }
    )
    await send({"type": "http.response.body", "body": _REFUSAL_BODY})


class BodyLimit:
    def __init__(
        self,
        app: ASGIApp,
        default: int = DEFAULT_BODY_LIMIT,
        routes: Mapping[str, int] | None = None,
    ) -> None:
        self.app, self.default = app, default
        self.routes = ROUTE_LIMITS if routes is None else routes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        path = str(scope.get("path", ""))
        if scope["type"] != "http" or _deferred(path):
            await self.app(scope, receive, send)
            return
        limit = self.routes.get(path, self.default)
        if _declared_too_large(scope, limit):
            await _refuse(scope, send)
            return

        if _chunked(scope):
            # No Content-Length to judge by: read the body up to the limit here, so a body over it
            # is refused before the auth middleware runs, exactly like one that declares its size
            # (at most ``limit`` bytes are buffered, which the app would buffer anyway), and replay
            # it to the app.
            buffered = await _read_up_to(receive, limit)
            if buffered is None:
                await _refuse(scope, send)
                return
            receive = _replay(buffered, receive)

        seen = 0
        tripped = False
        started = False

        async def counted() -> Message:
            nonlocal seen, tripped
            if tripped:
                return {"type": "http.disconnect"}
            message = await receive()
            if message["type"] == "http.request":
                seen += len(message.get("body", b""))
                if seen > limit:
                    tripped = True
                    return {"type": "http.disconnect"}
            return message

        async def guarded(message: Message) -> None:
            nonlocal started
            if tripped and not started:
                return  # whatever the app answers to a body it never fully got is dropped
            if message["type"] == "http.response.start":
                started = True
            await send(message)

        try:
            await self.app(scope, counted, guarded)
        except Exception:
            # An app that had a body cut under it may fail in any way (a disconnect, a 400 turned
            # into an exception group); the answer is the 413 either way. Anything else is raised.
            if not tripped:
                raise
        if tripped and not started:
            await _refuse(scope, send)
