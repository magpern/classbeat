"""The privacy-safe security log (ADR 0003 amendment "security log" of 2026-11-05, #632).

One ``security_log`` row per signed-in ``/api`` request that the general limit counted (its
``mark_counted``), written by ``AuthMiddleware`` once the response is known, separate from the
access log (which names no user). The limit's exempt paths (probes, sign-out) are never logged.
A row holds only:

- the user (``owner_id``) and the session's id, from the session the request already resolved
  (nothing is looked up for the log: a request whose session was not resolved is not logged);
- ``ip_hash``: HMAC under the ``security-log`` key of the coarse network (``bucket_ip``: IPv4 as it
  is, IPv6 by its /64), so rows of one network compare equal and the address is not stored;
- the method (one of ``METHODS``, else ``OTHER``), the **route template** the request matches
  among the app's own (``/api/classes/{class_id}``; ``unmatched`` for none), never the raw path or
  the query;
- the status and the time.

Never the address, ``User-Agent``, a cookie or token, the query string or the body: ``Entry`` has
no field for them and ``entry_for`` reads nothing else from the request
(``tests/test_security_log.py`` pins the fields, plants each in a request and searches the table
for it). The table's checks refuse a method, route or status of another shape.

The table is owned (ADR 0002): the row is written in the user's own session with a bare context
(``deps.security_log_context``). A failed write (any error) is logged by class (and SQLSTATE) and
never changes the response. A session writes at most one 429 row a minute; every other request is
within the general limit. The hourly purge drops rows older than ``CLASSBEAT_SECURITY_LOG_DAYS``.
"""

import logging
import re
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from fastapi import Request
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from starlette.routing import compile_path

from classbeat.auth.clientip import bucket_ip
from classbeat.auth.deps import cached_session, request_ip, security_log_context
from classbeat.auth.keys import Keys, hmac_digest
from classbeat.db import user_session
from classbeat.log import describe_database_error

logger = logging.getLogger(__name__)

METHODS = frozenset({"GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"})
UNMATCHED = "unmatched"
# The table's ``security_log_route`` check, in Python: lower-case path words and ``{name}``
# placeholders only, so a query string or an encoded value can never pass as a route.
ROUTE = re.compile(r"(/([a-z0-9_.-]+|\{[a-z_][a-z0-9_]*\}))+")
ROUTE_MAX = 200
_FOREIGN_KEY_VIOLATION = "23503"
_TEMPLATES = "security_log_templates"
_COUNTED = "security_log_counted"

# A 429 row carries its minute, so the partial unique index ``security_log_one_429`` keeps at most
# one per session and minute: past the limit a script adds no rows (T124). Every other request
# already counts against the general limit (``api_requests_per_hour`` per user).
_INSERT = text(
    "INSERT INTO security_log (owner_id, session_id, ip_hash, method, route, status, "
    "limited_minute) VALUES (:owner_id, :session_id, :ip_hash, :method, :route, :status, "
    "CASE WHEN :status = 429 THEN floor(extract(epoch FROM now()) / 60)::bigint END) "
    "ON CONFLICT (owner_id, session_id, limited_minute) WHERE limited_minute IS NOT NULL "
    "DO NOTHING"
)
_PURGE = text(
    "DELETE FROM security_log WHERE owner_id = :owner_id "
    "AND at < now() - make_interval(days => :days)"
)


@dataclass(frozen=True, slots=True, repr=False)
class Entry:
    """One row, before it is written. These fields are everything the log can hold."""

    user_id: UUID
    session_id: UUID
    ip_hash: bytes
    method: str
    route: str
    status: int

    def __repr__(self) -> str:  # never the hash
        return f"Entry({self.method} {self.route} {self.status})"


def network_hash(keys: Keys, ip: str) -> bytes:
    """The keyed hash of ``ip``'s coarse network (``bucket_ip``) under the ``security-log`` key."""
    return hmac_digest(keys.security_log, bucket_ip(ip))


def _templates(app: Any) -> list[tuple[re.Pattern[str], str, frozenset[str]]]:
    """``(regex, template, methods)`` of every API path, in route order, built once per app from
    its OpenAPI paths (the full templates, prefixes included) with Starlette's ``compile_path``."""
    cached = getattr(app.state, _TEMPLATES, None)
    if cached is None:
        cached = []
        try:
            paths = app.openapi().get("paths", {})
        except Exception as exc:  # never fail a request over the log
            # Not cached: the next request tries again rather than logging ``unmatched`` forever.
            logger.error("security log routes unavailable: %s", type(exc).__name__)
            return []
        for template, operations in paths.items():
            if len(template) <= ROUTE_MAX and ROUTE.fullmatch(template):
                regex = compile_path(template)[0]
                cached.append((regex, template, frozenset(name.upper() for name in operations)))
        setattr(app.state, _TEMPLATES, cached)
    return cached


def route_template(request: Request) -> str:
    """The template of the route that serves (or would serve) the request, or ``unmatched``."""
    return match_template(request.app, request.method, request.url.path)


def match_template(app: Any, method: str, path: str) -> str:
    """Matched against the app's own templates, never copied from the path, so the result is one
    of a fixed set whatever the client sends. It is found the same way whether the route ran or
    the middleware refused the request before routing (429, 403): the first route that answers
    the method, as Starlette picks it, else the first path match (a 405)."""
    fallback = UNMATCHED
    for regex, template, methods in _templates(app):
        if regex.match(path):
            if method in methods:
                return template
            if fallback == UNMATCHED:
                fallback = template
    return fallback


def mark_counted(request: Request) -> None:
    """Called by the general limit (``middleware.check_api_limit``) once it has counted the request
    against its buckets, allowed or refused. Nothing else marks a request."""
    setattr(request.state, _COUNTED, True)


def counted(request: Request) -> bool:
    """Whether the general limit counted this request: the log's only admission test."""
    return getattr(request.state, _COUNTED, False) is True


def entry_for(request: Request, status: int) -> Entry | None:
    """The row of a finished request, or ``None`` unless the general limit counted it (a signed-in
    ``/api`` request on a limited path). The log shares the limiter's decision instead of a copy of
    its path rules, so its rows stay within the limiter's budget and an exempt path (the probes,
    sign-out) is never logged."""
    if not counted(request):
        return None
    info = cached_session(request)
    if info is None:
        return None
    return Entry(
        user_id=info.user_id,
        session_id=info.id,
        ip_hash=network_hash(request.app.state.keys, request_ip(request)),
        method=request.method if request.method in METHODS else "OTHER",
        route=route_template(request),
        status=status,
    )


async def record(factory: async_sessionmaker[AsyncSession], entry: Entry) -> None:
    """Write ``entry`` in its user's own session. Any error (a driver error, a pool timeout, …) is
    logged by class, never raised: the log must not change the answer of a request that has
    already run (a user deleted by this very request fails the foreign key)."""
    try:
        async with user_session(factory, security_log_context(entry.user_id)) as db:
            await db.execute(
                _INSERT,
                {
                    "owner_id": entry.user_id,
                    "session_id": entry.session_id,
                    "ip_hash": entry.ip_hash,
                    "method": entry.method,
                    "route": entry.route,
                    "status": entry.status,
                },
            )
    except DBAPIError as exc:
        if getattr(exc.orig, "sqlstate", None) == _FOREIGN_KEY_VIOLATION:
            return  # the account is gone (deleted by this request): nothing to log it under
        logger.warning("security log write failed: %s", describe_database_error(exc))
    except Exception as exc:  # a pool timeout or anything else: never a 500
        # The route has already committed; the class name only (a message may hold values).
        logger.warning("security log write failed: %s", type(exc).__name__)


async def purge_owner(db: AsyncSession, owner_id: UUID, days: int) -> int:
    """This user's rows older than ``days``, in the purge's per-user session."""
    result = await db.execute(_PURGE, {"owner_id": owner_id, "days": days})
    return int(result.rowcount or 0)  # pyright: ignore[reportAttributeAccessIssue]
