"""The security headers every web-process response carries (ADR 0013 section 9; threat model
T20, T93 and T97).

- ``Content-Security-Policy``, the app's first, as a response header (never a ``<meta>`` in
  ``index.html``): everything from our own origin only, with one exception, album and playlist
  covers in ``img-src``: exactly the path-scoped Spotify CDN sources of
  ``classbeat.covers.IMG_SOURCES`` (ADR 0013 section 9 and its "CSP widening", #537). No inline
  or ``eval`` script, no inline ``<style>``, no plugins, no ``<base>``, forms only to us, and no
  framing by anyone. The one framing exception is the admin mail preview (ADR 0022 section 6,
  #653): ``frame-src`` is the path-scoped ``<public_base_url>/api/admin/mail/``, the only prefix a
  page may frame.
- With Turnstile on and not the fake (ADR 0025 section 10), ``https://challenges.cloudflare.com``
  joins ``script-src`` and ``frame-src`` (the widget's script and frame); nothing else changes (no
  nonce, no ``unsafe-inline``, no ``connect-src``).
- ``Referrer-Policy: no-referrer``, page-wide, so a cover request (or any link) carries no
  classbeat URL.

``SecurityHeaders`` is a plain ASGI middleware, so it adds both to every HTTP response (the SPA,
its files, the API, errors, an unhandled 500 included, and the SSE stream) without buffering a body.

The one response whose CSP it keeps is the mail preview's: a ``GET`` of exactly
``/api/admin/mail/{kind}/preview`` that is a 2xx and already carries its own
``Content-Security-Policy`` (``default-src 'none'`` ..., ``frame-ancestors 'self'``, ``sandbox``). A
401, 403, 404 or 500 from that route has no CSP of its own (or is the unhandled-error answer) and
gets the app's, so ``frame-ancestors 'none'`` holds for every response but the successful preview.
"""

import re
from typing import Any, Final

from starlette.datastructures import MutableHeaders
from starlette.requests import Request
from starlette.responses import PlainTextResponse, Response
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from classbeat.covers import IMG_SOURCES

# The only prefix a page may frame: the admin mail preview (ADR 0022 section 6).
PREVIEW_PREFIX: Final = "/api/admin/mail/"
# Cloudflare Turnstile's script and frame origin (ADR 0025 section 10), only while it is on.
TURNSTILE_SOURCE: Final = "https://challenges.cloudflare.com"
_PREVIEW_PATH: Final = re.compile(r"^/api/admin/mail/[a-z][a-z_]{0,39}/preview$")

_DIRECTIVES_BEFORE_SCRIPT: Final = ("default-src 'self'",)
_DIRECTIVES_BEFORE_FRAME: Final = (
    "style-src 'self'",
    " ".join(("img-src 'self'", *IMG_SOURCES)),
    "font-src 'self'",
    "connect-src 'self'",
    "media-src 'none'",
    "object-src 'none'",
)
_DIRECTIVES_AFTER_FRAME: Final = (
    "worker-src 'none'",
    "manifest-src 'self'",
    "base-uri 'none'",
    "form-action 'self'",
    "frame-ancestors 'none'",
)


def csp_directives(base_url: str | None = None, *, turnstile: bool = False) -> tuple[str, ...]:
    """The app's directives; ``frame-src`` is ``'none'`` without a base URL, else the path-scoped
    preview prefix under it (a CSP source with a path ending in ``/`` matches that prefix). With
    ``turnstile`` Cloudflare's origin joins ``script-src`` and ``frame-src``."""
    extra = (TURNSTILE_SOURCE,) if turnstile else ()
    script = " ".join(("script-src 'self'", *extra))
    framed = (*extra, *(() if base_url is None else (f"{base_url}{PREVIEW_PREFIX}",)))
    frame = " ".join(("frame-src", *framed)) if framed else "frame-src 'none'"
    return (
        *_DIRECTIVES_BEFORE_SCRIPT,
        script,
        *_DIRECTIVES_BEFORE_FRAME,
        frame,
        *_DIRECTIVES_AFTER_FRAME,
    )


def content_security_policy(base_url: str | None = None, *, turnstile: bool = False) -> str:
    return "; ".join(csp_directives(base_url, turnstile=turnstile))


CSP_DIRECTIVES: Final = csp_directives()
CONTENT_SECURITY_POLICY: Final = content_security_policy()
REFERRER_POLICY: Final = "no-referrer"


def preview_keeps_own_csp(scope: Scope, status: int, headers: MutableHeaders) -> bool:
    """Whether the response is the mail preview's own successful answer, whose CSP stays."""
    return (
        scope.get("method") == "GET"
        and _PREVIEW_PATH.fullmatch(str(scope.get("path", ""))) is not None
        and 200 <= status < 300
        and "content-security-policy" in headers
    )


def unhandled_error_handler(base_url: str | None = None, *, turnstile: bool = False) -> Any:
    csp = content_security_policy(base_url, turnstile=turnstile)

    async def handler(_request: Request, _exc: Exception) -> Response:
        return await unhandled_error(_request, _exc, csp)

    return handler


async def unhandled_error(
    _request: Request, _exc: Exception, csp: str = CONTENT_SECURITY_POLICY
) -> Response:
    """The answer to an exception no route handled (registered for ``Exception``).

    Starlette's ``ServerErrorMiddleware`` sits outside every middleware we add, so without this
    its plain 500 would leave without the two headers (T93). The body is the one it sends itself.
    """
    return PlainTextResponse(
        "Internal Server Error",
        status_code=500,
        headers={
            "Content-Security-Policy": csp,
            "Referrer-Policy": REFERRER_POLICY,
        },
    )


class SecurityHeaders:
    def __init__(self, app: ASGIApp, base_url: str | None = None, turnstile: bool = False) -> None:
        self.app = app
        self.csp = content_security_policy(base_url, turnstile=turnstile)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                if not preview_keeps_own_csp(scope, message["status"], headers):
                    headers["Content-Security-Policy"] = self.csp
                headers["Referrer-Policy"] = REFERRER_POLICY
            await send(message)

        await self.app(scope, receive, with_headers)
