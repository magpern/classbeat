"""Error categories and the wrapper around every provider-library call (ADR 0004 §5).

The category comes from the exception's type and its ``__cause__`` only, never from the message.
The library's exception never leaves the wrapper: the ``except`` block records the category, the
class name and the HTTP status into locals, and ``ConnectionFailure(category)`` is raised
**after** the block, so it has neither ``__cause__`` nor ``__context__`` and its ``args`` are
``(category,)``. Nothing logs, stores or returns ``str(exc)``, ``exc.args``, a response body, the
``HTTPError``'s URL or a traceback of a library exception: a failed sign-in can put the MOWL
login ticket into the text and the URL, and the sign-in's frames hold the password.

No frame locals anywhere: no error reporter, debugger page or logging option that captures them
is ever enabled (``ConnectionFailure.__traceback__`` still holds the wrapper's frame).
"""

import asyncio
import logging
import socket
from collections.abc import Callable
from dataclasses import dataclass
from urllib.error import HTTPError, URLError

from mowl.errors import MowlError

from classbeat.egress import OutboundBlocked

logger = logging.getLogger(__name__)

CATEGORIES = (
    "invalid_credentials",
    "needs_reconnect",
    "rate_limited",
    "upstream_unavailable",
    "key_unavailable",
    "decrypt_failed",
    "not_connected",
    "unknown",
)
MAX_RETRY_AFTER = 86_400  # a day; larger hints are clamped
_RETRY_DIGITS = 6
_CHAIN_LIMIT = 64  # exceptions looked at in one chain


class ConnectionFailure(Exception):
    """A provider or connection failure. ``args == (category,)``; ``retry_after`` (seconds) only
    for ``rate_limited`` when the provider sent an integer ``Retry-After``."""

    def __init__(self, category: str, retry_after: int | None = None) -> None:
        if category not in CATEGORIES:
            category = "unknown"
        super().__init__(category)
        self.category = category
        self.retry_after = retry_after


@dataclass(frozen=True, slots=True)
class Outcome:
    """What the ``except`` block keeps: no message, no URL, no exception object."""

    category: str
    error_class: str
    http_status: int | None = None
    retry_after: int | None = None


def _retry_after(cause: object) -> int | None:
    """``Retry-After`` as a whole number of seconds; an HTTP date or anything else is no hint."""
    headers = getattr(cause, "headers", None)
    getter = getattr(headers, "get", None)
    if getter is None:
        return None
    value = getter("Retry-After")
    if not isinstance(value, str):
        return None
    value = value.strip()
    if not value or len(value) > _RETRY_DIGITS or not all("0" <= c <= "9" for c in value):
        return None
    return min(int(value), MAX_RETRY_AFTER)


def _by_status(status: object, cause: object, *, sign_in: bool) -> tuple[str, int | None]:
    if not isinstance(status, int) or isinstance(status, bool):
        return "unknown", None
    if status in (401, 403):
        return ("invalid_credentials" if sign_in else "needs_reconnect"), None
    if status == 429:
        return "rate_limited", _retry_after(cause)
    if 500 <= status <= 599:
        return "upstream_unavailable", None
    return "unknown", None


def _classify(exc: BaseException, library_error: type[BaseException], sign_in: bool) -> Outcome:
    name = type(exc).__name__
    if isinstance(exc, library_error):
        cause = exc.__cause__
        if isinstance(cause, HTTPError):
            status = getattr(cause, "code", None)
            category, retry = _by_status(status, cause, sign_in=sign_in)
            return Outcome(category, name, status if isinstance(status, int) else None, retry)
        # No HTTP cause: the sign-in refused (MOWL's own refusal), or something unknown.
        return Outcome("invalid_credentials" if sign_in else "unknown", name)
    # Most specific first: HTTPError is a subclass of URLError.
    if isinstance(exc, HTTPError):
        status = getattr(exc, "code", None)
        category, retry = _by_status(status, exc, sign_in=sign_in)
        return Outcome(category, name, status if isinstance(status, int) else None, retry)
    if isinstance(exc, URLError | TimeoutError | socket.timeout | ConnectionError):
        return Outcome("upstream_unavailable", name)
    return Outcome("unknown", name)


def classify(
    exc: BaseException, *, library_error: type[BaseException] = MowlError, sign_in: bool
) -> Outcome:
    """The category of ``exc``. Never raises: any error while classifying gives ``unknown``."""
    try:
        return _classify(exc, library_error, sign_in)
    except Exception:
        return Outcome("unknown", "unclassifiable")


def log_outcome(outcome: Outcome, action: str) -> None:
    """Category, class name and status only."""
    logger.warning(
        "provider call failed",
        extra={
            "action": action,
            "category": outcome.category,
            "error_class": outcome.error_class,
            "http_status": outcome.http_status,
        },
    )


def _outbound_blocked(exc: BaseException) -> OutboundBlocked | None:
    """The allowlist refusal anywhere in ``exc``'s chain: itself, or any ``__cause__`` or
    ``__context__`` (both links followed, cycles and depth bounded), since a library may wrap it
    or raise something else while handling it."""
    pending: list[BaseException] = [exc]
    seen: set[int] = set()
    while pending and len(seen) < _CHAIN_LIMIT:
        current = pending.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))
        if isinstance(current, OutboundBlocked):
            return current
        pending += [e for e in (current.__cause__, current.__context__) if e is not None]
    return None


async def run_library_call[T](
    call: Callable[[], T], *, library_error: type[BaseException] = MowlError, sign_in: bool
) -> T:
    """Run a synchronous library call in a worker thread (``asyncio.to_thread``).

    ``library_error`` is the library's own error class, ``mowl.errors.MowlError`` (tests pass
    their own). Any ``Exception`` becomes ``ConnectionFailure(category)`` with no chain, except
    a request the worker's outbound allowlist refused (ADR 0006 §9): that is raised again as a
    fresh ``OutboundBlocked`` (host only, no chain), so the job fails ``outbound_blocked`` and the
    connection's ``last_error`` is not touched (the refusal is not the connection's fault).
    """
    outcome: Outcome | None = None
    blocked_host: str | None = None
    try:
        return await asyncio.to_thread(call)
    except Exception as exc:
        found = _outbound_blocked(exc)
        if found is not None:
            blocked_host = found.host
        else:
            outcome = classify(exc, library_error=library_error, sign_in=sign_in)
    # Outside the except block: no __context__. ``exc`` is already unbound here.
    if blocked_host is not None:
        raise OutboundBlocked(blocked_host)
    assert outcome is not None  # noqa: S101 - set in the except block
    log_outcome(outcome, "sign_in" if sign_in else "use")
    raise ConnectionFailure(outcome.category, outcome.retry_after)
