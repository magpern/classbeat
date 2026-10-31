"""Spotify's error categories and what the page says (ADR 0009 section 8).

A category comes from the exception's **type** and its HTTP status (the ``__cause__``
``HTTPError`` the library chains, or the library's own ``status``), and for the token endpoint
from the short OAuth ``error`` code ``classbeat.spotify.http`` reads; never from message text.
Most specific first. ``Retry-After`` is a whole number of seconds; above an hour a 429 is
``quota_exhausted`` instead of ``rate_limited``.

Only ``invalid_grant`` and ``app_misconfigured`` from the token endpoint, and ``invalid_grant`` on
the repeat of a read after a refresh, flip a connection (``classbeat.spotify.access``); the other
categories leave its status alone.
"""

import socket
from typing import Final
from urllib.error import HTTPError, URLError

import httpx
from mowl.errors import MowlError

from classbeat.connections.errors import Outcome, retry_after_of

# ADR 0009 section 8, in the table's order.
CATEGORIES: Final = (
    "invalid_grant",
    "app_misconfigured",
    "not_allowlisted",
    "forbidden",
    "rate_limited",
    "quota_exhausted",
    "upstream_unavailable",
    "not_found",
    "scope_mismatch",
    "invalid_response",
    "account_changed",
    "access_denied",
)
QUOTA_THRESHOLD_SECONDS: Final = 3600
# Retried by a job (with backoff, at least the hint); the others are not.
RETRYABLE: Final = frozenset({"rate_limited", "upstream_unavailable"})
# The OAuth ``error`` codes of the token endpoint (and of the callback's ``error``) that mean the
# app is set up wrong; ``invalid_grant`` is the refresh token's or the code's own refusal.
APP_ERROR_CODES: Final = frozenset(
    {
        "invalid_client",
        "invalid_request",
        "unauthorized_client",
        "unsupported_response_type",
        "invalid_scope",
    }
)

_SCOPE_SENTENCE = "Spotify did not give the access classbeat needs. Connect again and allow it."
SENTENCES: Final[dict[str, str]] = {
    "invalid_grant": "Spotify no longer accepts the connection. Reconnect Spotify.",
    "app_misconfigured": (
        "Spotify does not recognise this app setup. Check the Client ID and the redirect URI in "
        "your Spotify app."
    ),
    "not_allowlisted": (
        "Spotify refused this account. In your Spotify app's User Management add this "
        "account's name and email (Development Mode allows 5 people), and make sure the app's "
        "owner has Spotify Premium."
    ),
    "forbidden": "Spotify refused this request for your account or playlist.",
    "rate_limited": "Spotify asks to slow down; trying again in about {minutes}.",
    "quota_exhausted": (
        "Your Spotify app has used up its allowance for now. Try again in about {hours}."
    ),
    "upstream_unavailable": "Spotify is not answering.",
    "not_found": "Spotify could not find that playlist.",
    "scope_mismatch": _SCOPE_SENTENCE,
    "invalid_response": _SCOPE_SENTENCE,
    "account_changed": (
        "This is a different Spotify account than before. Confirm to switch (classbeat forgets "
        "the working playlist)."
    ),
    "access_denied": "Spotify was not connected.",
}
UNKNOWN_SENTENCE: Final = "Something went wrong with Spotify. Try again later."


def _plural(n: int, unit: str) -> str:
    return f"{n} {unit}" if n == 1 else f"{n} {unit}s"


def sentence(category: str, retry_after: int | None = None) -> str:
    """The plain sentence of ``category`` (a provider message is never shown)."""
    text = SENTENCES.get(category)
    if text is None:
        return UNKNOWN_SENTENCE
    seconds = retry_after if retry_after is not None and retry_after > 0 else 60
    minutes = max(1, -(-seconds // 60))
    hours = max(1, -(-seconds // 3600))
    return text.format(minutes=_plural(minutes, "minute"), hours=_plural(hours, "hour"))


def by_status(status: int, retry_after: int | None, *, connect: bool) -> str:
    """The category of an API answer's HTTP status. ``connect``: the ``GET /me`` of a connect,
    where a 403 is the allow-list (or the app owner's Premium) and a 401 the new token's
    refusal."""
    if status == 401:
        return "invalid_grant"
    if status == 403:
        return "not_allowlisted" if connect else "forbidden"
    if status == 404:
        return "not_found"
    if status == 429:
        if retry_after is not None and retry_after > QUOTA_THRESHOLD_SECONDS:
            return "quota_exhausted"
        return "rate_limited"
    if 500 <= status <= 599:
        return "upstream_unavailable"
    return "invalid_response"


def by_oauth_code(status: int, code: object, retry_after: int | None) -> str:
    """The category of a token endpoint's error answer: its OAuth ``error`` code (a short
    enumerated value, never its description), else its status.

    Only a recognised code says what is wrong (``invalid_grant``, ``APP_ERROR_CODES``). A 400 or
    401 **without** one (no JSON, no ``error``, or a code not listed) is not Spotify's OAuth
    answer about this app or token, so it neither names a misconfiguration nor flips the
    connection: ``upstream_unavailable``, retried like an outage (ADR 0009 section 8; #198 item
    2)."""
    if status == 429 or 500 <= status <= 599:
        return by_status(status, retry_after, connect=False)
    if code == "invalid_grant":
        return "invalid_grant"
    if isinstance(code, str) and code in APP_ERROR_CODES:
        return "app_misconfigured"
    if status in (400, 401):
        return "upstream_unavailable"
    return "invalid_response"


def _status_of(exc: BaseException) -> tuple[int | None, object]:
    """The HTTP status and where its headers live: the chained ``HTTPError`` first."""
    cause = exc.__cause__
    if isinstance(cause, HTTPError):
        code = getattr(cause, "code", None)
        return (code if isinstance(code, int) and not isinstance(code, bool) else None), cause
    status = getattr(exc, "status", None)
    if isinstance(status, int) and not isinstance(status, bool):
        return status, None
    return None, None


def classify_use(exc: BaseException) -> Outcome:
    """A library call's failure (``run_library_call``'s ``classifier``), most specific first."""
    name = type(exc).__name__
    if isinstance(exc, MowlError):
        status, headers = _status_of(exc)
        if status is None:
            if isinstance(
                exc.__cause__, URLError | TimeoutError | socket.timeout | ConnectionError
            ):
                return Outcome("upstream_unavailable", name)
            # The library refused the answer itself (no valid id, not a page): no HTTP cause.
            return Outcome("invalid_response", name)
        hint = retry_after_of(headers) if headers is not None else None
        if hint is None:
            given = getattr(exc, "retry_after", None)
            hint = given if isinstance(given, int) and not isinstance(given, bool) else None
        return Outcome(by_status(status, hint, connect=False), name, status, hint)
    if isinstance(exc, HTTPError):
        code = getattr(exc, "code", None)
        if isinstance(code, int) and not isinstance(code, bool):
            hint = retry_after_of(exc)
            return Outcome(by_status(code, hint, connect=False), name, code, hint)
        return Outcome("invalid_response", name)
    if isinstance(
        exc, URLError | TimeoutError | socket.timeout | ConnectionError | httpx.TransportError
    ):
        return Outcome("upstream_unavailable", name)
    return Outcome("unknown", name)
