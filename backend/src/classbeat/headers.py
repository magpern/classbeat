"""The security headers every web-process response carries (ADR 0013 section 9; threat model
T20, T93 and T97).

- ``Content-Security-Policy``, the app's first, as a response header (never a ``<meta>`` in
  ``index.html``): everything from our own origin only, with one exception, album covers from
  ``https://i.scdn.co`` in ``img-src`` (``classbeat.covers``). No inline or ``eval`` script, no
  inline ``<style>``, no plugins, no ``<base>``, forms only to us, and no framing by anyone.
- ``Referrer-Policy: no-referrer``, page-wide, so a cover request (or any link) carries no
  classbeat URL.

``SecurityHeaders`` is a plain ASGI middleware, so it adds both to every HTTP response (the SPA,
its files, the API, errors, an unhandled 500 included, and the SSE stream) without buffering a body.
"""

from typing import Final

from starlette.datastructures import MutableHeaders
from starlette.requests import Request
from starlette.responses import PlainTextResponse, Response
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from classbeat.covers import COVER_HOST

CSP_DIRECTIVES: Final = (
    "default-src 'self'",
    "script-src 'self'",
    "style-src 'self'",
    f"img-src 'self' {COVER_HOST}",
    "font-src 'self'",
    "connect-src 'self'",
    "media-src 'none'",
    "object-src 'none'",
    "frame-src 'none'",
    "worker-src 'none'",
    "manifest-src 'self'",
    "base-uri 'none'",
    "form-action 'self'",
    "frame-ancestors 'none'",
)
CONTENT_SECURITY_POLICY: Final = "; ".join(CSP_DIRECTIVES)
REFERRER_POLICY: Final = "no-referrer"


async def unhandled_error(_request: Request, _exc: Exception) -> Response:
    """The answer to an exception no route handled (registered for ``Exception``).

    Starlette's ``ServerErrorMiddleware`` sits outside every middleware we add, so without this
    its plain 500 would leave without the two headers (T93). The body is the one it sends itself.
    """
    return PlainTextResponse(
        "Internal Server Error",
        status_code=500,
        headers={
            "Content-Security-Policy": CONTENT_SECURITY_POLICY,
            "Referrer-Policy": REFERRER_POLICY,
        },
    )


class SecurityHeaders:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                headers["Content-Security-Policy"] = CONTENT_SECURITY_POLICY
                headers["Referrer-Policy"] = REFERRER_POLICY
            await send(message)

        await self.app(scope, receive, with_headers)
