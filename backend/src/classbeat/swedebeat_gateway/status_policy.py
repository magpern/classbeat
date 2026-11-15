"""A gateway answer's status to a category (ADR 0035 sections 3, 5 and 6). Pure: no I/O.

The category comes from the status and, for a ``401`` only, from the string ``detail``: the
gateway's ``401 {"detail": "unknown client"}`` (a bad or missing ``X-API-Key``) is classbeat's
configuration error, any other ``401`` is the user's. That comparison is exact, and only when
``detail`` is a string: a schema-422 list or any non-string ``detail`` is never compared. No
message is ever kept, stored or logged.

**Login** (section 3): ``200`` with a valid body is connected (``None``); ``401 unknown client``
and ``403`` (the gateway's login never answers 403: a proxy or deny in front of it) are
``app_key_unavailable``; another ``401`` and ``422`` are ``invalid_credentials``; ``429`` is
``rate_limited``; ``5xx`` (and a timeout or refused connection, decided by the caller) is
``upstream_unavailable``; ``413``, another status, a bad body and a redirect are ``unknown``.

**Session and match** (sections 5 and 6): ``401 unknown client`` and ``403`` are
``app_misconfigured``; another ``401`` is ``bearer_refused`` (the bearer was refused; the match
confirms with one ``GET /auth/session`` before it flips the connection, SG-2); ``429`` is
``rate_limited``; ``5xx`` is ``upstream_unavailable``; a ``3xx`` is ``redirect`` (the caller
raises ``OutboundBlocked``: ``outbound_blocked``); ``413``, ``422``, another status and a bad body
are ``invalid_response``.
"""

import json
from typing import Final, Literal, get_args

UNKNOWN_CLIENT: Final = "unknown client"

LoginCategory = Literal[
    "app_key_unavailable", "invalid_credentials", "rate_limited", "upstream_unavailable", "unknown"
]
CallCategory = Literal[
    "app_misconfigured",
    "bearer_refused",
    "rate_limited",
    "upstream_unavailable",
    "invalid_response",
    "redirect",
]
LOGIN_CATEGORIES: Final = frozenset(get_args(LoginCategory))
# ``redirect`` is never a failure's category: the caller raises ``OutboundBlocked`` for it.
CALL_CATEGORIES: Final = frozenset(get_args(CallCategory)) - {"redirect"}


def is_unknown_client(body: bytes | None) -> bool:
    """The body is ``{"detail": "unknown client"}``'s shape: an object whose ``detail`` is the
    string ``unknown client`` exactly. Any other body, a list ``detail`` or a non-string is not."""
    if body is None:
        return False
    try:
        answer = json.loads(body)
    except (ValueError, RecursionError):
        return False
    if not isinstance(answer, dict):
        return False
    detail: object = answer.get("detail")  # pyright: ignore[reportUnknownMemberType]
    return isinstance(detail, str) and detail == UNKNOWN_CLIENT


def login_category(status: int, *, unknown_client: bool, valid_body: bool) -> LoginCategory | None:
    """``None`` for a login that connected, else its category (section 3's table)."""
    if status == 200:
        return None if valid_body else "unknown"
    if status == 401:
        return "app_key_unavailable" if unknown_client else "invalid_credentials"
    if status == 403:
        return "app_key_unavailable"
    if status == 422:
        return "invalid_credentials"
    if status == 429:
        return "rate_limited"
    if 500 <= status <= 599:
        return "upstream_unavailable"
    return "unknown"


def call_category(status: int, *, unknown_client: bool, valid_body: bool) -> CallCategory | None:
    """``None`` for a session check or a match that answered, else its category (sections 5
    and 6)."""
    if status == 200:
        return None if valid_body else "invalid_response"
    if status == 401:
        return "app_misconfigured" if unknown_client else "bearer_refused"
    if status == 403:
        return "app_misconfigured"
    if status == 429:
        return "rate_limited"
    if 500 <= status <= 599:
        return "upstream_unavailable"
    if 300 <= status <= 399:
        return "redirect"
    return "invalid_response"
