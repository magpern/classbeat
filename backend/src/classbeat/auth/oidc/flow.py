"""What the authorize, continue and consent steps share (ADR 0034 section 1): the provider, the
``__Host-cb_oidc`` flow cookie, the redirect to the registered address and the HTML pages.

The flow cookie is 32 random bytes (base64url), ``Secure``, ``HttpOnly``, ``SameSite=Lax``,
``Path=/``, ``Max-Age=600``. A browser that already has a well-formed one keeps its value, and
every new authorize request renews its ``Max-Age`` (#1056 N16), so the cookie outlives each of
its requests. Only its SHA-256 is stored.

The pages are plain HTML (the global CSP holds: no script, no style, ``frame-ancestors 'none'``)
and are never cached. They never name the client's address: a page is what a request gets when
it must **not** be redirected.
"""

import re
from typing import Final
from urllib.parse import urlencode

from fastapi import HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from starlette.responses import Response

from classbeat.auth.keys import new_token, token_hash
from classbeat.auth.oidc.provider import Provider

FLOW_COOKIE: Final = "__Host-cb_oidc"
FLOW_MAX_AGE: Final = 10 * 60
NO_STORE: Final = {"Cache-Control": "no-store"}
_COOKIE_VALUE = re.compile(r"[A-Za-z0-9_-]{43}")

EXPIRED_PAGE: Final = "This sign-in request has expired. Start again from the player."
INVALID_PAGE: Final = "This sign-in link is not valid. Start again from the player."


def provider_of(request: Request) -> Provider:
    """The configured provider, or 404 when it is off."""
    provider: Provider | None = getattr(request.app.state, "oidc", None)
    if provider is None:
        raise HTTPException(status_code=404)
    return provider


def flow_cookie(request: Request) -> str | None:
    value = request.cookies.get(FLOW_COOKIE)
    return value if value is not None and _COOKIE_VALUE.fullmatch(value) else None


def browser_hash(cookie: str) -> bytes:
    return token_hash(cookie)


def keep_or_new_cookie(request: Request) -> str:
    return flow_cookie(request) or new_token()


def set_flow_cookie(response: Response, value: str) -> None:
    response.set_cookie(
        FLOW_COOKIE,
        value,
        max_age=FLOW_MAX_AGE,
        path="/",
        secure=True,
        httponly=True,
        samesite="lax",
    )


def client_redirect(redirect_uri: str, **params: str) -> str:
    """The registered address with ``params`` added to its query."""
    joiner = "&" if "?" in redirect_uri else "?"
    return f"{redirect_uri}{joiner}{urlencode(params)}"


def redirect(location: str) -> RedirectResponse:
    return RedirectResponse(location, status_code=302, headers=NO_STORE)


def page(message: str, status: int = 400) -> HTMLResponse:
    body = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex">
<title>classbeat sign-in</title>
</head>
<body>
<main>
<h1>classbeat sign-in</h1>
<p>{message}</p>
<p><a href="/">Go to classbeat</a></p>
</main>
</body>
</html>
"""
    return HTMLResponse(body, status_code=status, headers=NO_STORE)
