"""The Spotify token refresh (ADR 0009 section 6; ADR 0004 section 3's requirements, one by one).

The library never refreshes (``credentials.build_credentials`` gives it the access token only),
so this is the only code that turns a refresh token into new tokens and stores them:

- **One at a time per user, the lock covering the refresh itself.** A short transaction takes
  ``SELECT ... FOR UPDATE`` on the user's Spotify row and runs the token request (``exchange``,
  ``classbeat.spotify.http``, total deadline ``CLASSBEAT_SPOTIFY_REFRESH_TIMEOUT_SECONDS``)
  **inside it**. A second caller waits for the lock at most ``lock_timeout`` (the role's 5 s),
  then reads again once; if it still cannot lock it fails ``upstream_unavailable`` (retryable)
  instead of refreshing in parallel.
- **After the lock, re-read and reuse.** If the row now expires beyond the margin (or, for a
  refresh forced by a 401, holds another access token than the one that was refused), another
  caller refreshed: no request.
- **Rotation is persisted before the lock is released.** The new access token, the new refresh
  token when the answer has one (else the old one stays) and ``expires_at`` are encrypted under a
  new DEK (same ``id``, same ``generation``) and written in that transaction. A failed write
  fails the call; the rollback releases the lock.
- **Conditional on the version read.** The ``UPDATE`` is ``WHERE id AND generation`` (a reconnect
  in between wins; zero rows is ``not_connected``).
- **Failure.** ``invalid_grant`` or ``app_misconfigured`` from the token endpoint flips the row
  to ``needs_reconnect`` in the same transaction, under the lock, conditional on ``id`` and
  ``generation`` (the refresh token it concerns is the one the row holds now); tokens are kept.
  Every other category leaves the row alone.

Nothing here logs a token, a body or an exception text.
"""

import json
import logging
import math
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, NoReturn

from sqlalchemy import func, select, text, update
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from classbeat.connections.errors import ConnectionFailure
from classbeat.connections.repository import SPOTIFY_FLIP_CATEGORIES, _decrypt, _flip  # pyright: ignore[reportPrivateUsage]
from classbeat.connections.tables import connections
from classbeat.context import UserContext
from classbeat.crypto.envelope import canonical_json, connection_aad, connection_wrap_aad, seal
from classbeat.crypto.keks import KeyRing
from classbeat.db import user_session

logger = logging.getLogger(__name__)

PROVIDER = "spotify"
LOCK_NOT_AVAILABLE = "55P03"  # SQLSTATE of a lock_timeout
# How long a second caller waits for the row lock: the role's ``lock_timeout`` (ADR 0002 section
# 5), set explicitly for the refresh transaction so the wait is this module's own decision.
LOCK_TIMEOUT = "5s"
_SET_LOCK_TIMEOUT = text("SELECT set_config('lock_timeout', :value, true)")
# ADR 0009 section 5 step 3: the stored expiry is the answer's ``expires_in`` minus 60 s.
EXPIRY_SLACK_SECONDS = 60
_C = connections.c
Factory = async_sessionmaker[AsyncSession]


@dataclass(frozen=True, slots=True, eq=False)
class RefreshedTokens:
    """A token endpoint's answer to a refresh. ``refresh_token`` is ``None`` when Spotify did
    not rotate it. Never printed."""

    access_token: str
    refresh_token: str | None
    expires_in: int

    def __repr__(self) -> str:
        return "RefreshedTokens(...)"

    __str__ = __repr__

    def __reduce__(self) -> NoReturn:
        raise TypeError("tokens are never serialized")


# (client_id, refresh_token) -> the new tokens, or ``ConnectionFailure(category)``.
Exchange = Callable[[str, str], Awaitable[RefreshedTokens]]


def expires_at_of(expires_in: int, now: float) -> int:
    """Epoch seconds at which the access token is treated as expired."""
    return math.floor(now) + max(int(expires_in) - EXPIRY_SLACK_SECONDS, 0)


def _lock_timed_out(error: DBAPIError) -> bool:
    return getattr(error.orig, "sqlstate", None) == LOCK_NOT_AVAILABLE


async def refresh_spotify(
    factory: Factory,
    ring: KeyRing,
    ctx: UserContext,
    exchange: Exchange,
    *,
    margin_seconds: float,
    stale_access_token: str | None = None,
    clock: Callable[[], float] = time.time,
) -> bool:
    """Refresh the user's Spotify tokens if they still need it; True when a token request was
    sent. ``stale_access_token`` (a 401 forced this refresh): refresh only while the row still
    holds that access token. ``ConnectionFailure`` on any failure, with no chained exception."""
    for attempt in (1, 2):
        try:
            return await _refresh_once(
                factory, ring, ctx, exchange, margin_seconds, stale_access_token, clock
            )
        except DBAPIError as error:
            if not _lock_timed_out(error):
                raise
            logger.info("spotify refresh waited for the row lock", extra={"attempt": attempt})
    raise ConnectionFailure("upstream_unavailable")


async def _refresh_once(
    factory: Factory,
    ring: KeyRing,
    ctx: UserContext,
    exchange: Exchange,
    margin_seconds: float,
    stale_access_token: str | None,
    clock: Callable[[], float],
) -> bool:
    failure: ConnectionFailure | None = None
    sent = False
    async with user_session(factory, ctx) as db:
        await db.execute(_SET_LOCK_TIMEOUT, {"value": LOCK_TIMEOUT})
        row = (
            await db.execute(
                select(
                    _C.id,
                    _C.status,
                    _C.public,
                    _C.secret,
                    _C.secret_nonce,
                    _C.wrapped_dek,
                    _C.dek_nonce,
                    _C.key_id,
                    _C.generation,
                )
                .where(_C.owner_id == ctx.user_id, _C.provider == PROVIDER)
                .with_for_update()
            )
        ).first()
        if row is None or row.status != "connected":
            raise ConnectionFailure("not_connected")
        problem, plaintext = await _decrypt(db, ring, ctx, PROVIDER, row)
        if problem is not None or plaintext is None:
            # ``open_connection`` flips an undecryptable row; this path only refuses.
            raise ConnectionFailure(problem or "unknown")
        payload = _payload(plaintext)
        if stale_access_token is not None:
            if payload["access_token"] != stale_access_token:
                return False  # another caller refreshed after the refused call opened the row
        elif float(payload["expires_at"]) - clock() >= margin_seconds:
            return False  # another caller refreshed while this one waited for the lock
        client_id = (row.public or {}).get("client_id")
        if not isinstance(client_id, str) or not client_id:
            raise ConnectionFailure("app_misconfigured")
        sent = True
        answer: RefreshedTokens | None = None
        try:
            answer = await exchange(client_id, str(payload["refresh_token"]))
        except ConnectionFailure as error:
            failure = ConnectionFailure(
                error.category, error.retry_after, not_applied=True, http_status=error.http_status
            )
        if answer is not None:
            new_payload = {
                "access_token": answer.access_token,
                "refresh_token": answer.refresh_token or payload["refresh_token"],
                "expires_at": expires_at_of(answer.expires_in, clock()),
            }
            if not await _store(db, ring, ctx, row, new_payload):
                raise ConnectionFailure("not_connected")
        elif failure is not None and failure.category in SPOTIFY_FLIP_CATEGORIES:
            # Under the lock, after the re-read: the refresh token refused is the row's own.
            await _flip(db, ctx, row.id, int(row.generation), failure.category)
    if failure is not None:
        logger.warning("spotify refresh failed", extra={"category": failure.category})
        raise failure
    return sent


def _payload(plaintext: bytes) -> dict[str, Any]:
    try:
        payload = json.loads(plaintext)
    except ValueError:
        payload = None
    if (
        not isinstance(payload, dict)
        or not isinstance(payload.get("access_token"), str)
        or not isinstance(payload.get("refresh_token"), str)
        or not isinstance(payload.get("expires_at"), int | float)
    ):
        raise ConnectionFailure("unknown")
    return payload  # pyright: ignore[reportUnknownVariableType]


async def _store(
    db: AsyncSession, ring: KeyRing, ctx: UserContext, row: Any, payload: dict[str, Any]
) -> bool:
    """The rotated tokens under a new DEK, same ``id`` and ``generation``; False when the row is
    no longer the generation that was read."""
    kek = await ring.current(db)
    sealed = seal(
        canonical_json(payload),
        connection_aad(ctx.user_id, PROVIDER, row.id),
        kek,
        connection_wrap_aad(kek.key_id, ctx.user_id, PROVIDER, row.id),
    )
    result = await db.execute(
        update(connections)
        .where(
            _C.id == row.id,
            _C.owner_id == ctx.user_id,
            _C.generation == row.generation,
        )
        .values(
            secret=sealed.ciphertext,
            secret_nonce=sealed.nonce,
            wrapped_dek=sealed.wrapped_dek,
            dek_nonce=sealed.dek_nonce,
            key_id=sealed.key_id,
            updated_at=func.now(),
        )
        .returning(_C.id)
    )
    return result.first() is not None
